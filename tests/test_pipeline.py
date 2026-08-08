"""Pipeline tests: the builder/verifier bounce loop, the capstone gate
(build -> verify -> pdflatex PDF), and run_full end-to-end orchestration."""
import json
import tempfile
import uuid

import pytest

from resume_tailor.collector.embed import FakeEmbedder
from resume_tailor.collector.vector_store import VectorStore
from resume_tailor.jd_parser import parse_jd
from resume_tailor.llm import MockLLMClient
from resume_tailor.matcher import MatchConfig, match_jd
from resume_tailor.pipeline import build_verified_resume, run_full
from resume_tailor.schemas import (
    Evidence,
    EvidenceChunk,
    EvidenceGraph,
    ResumeHeader,
)
from resume_tailor.verifier import iter_bullets

EID = "repo:acme#file:queue.py#L45"
SNIPPET = "Built a Redis-backed job queue with retries"


def _corpus(tmp_path=None):
    """A tiny evidence corpus: graph + Chroma store, all in-memory/tmp."""
    graph = EvidenceGraph()
    graph.add_source(Evidence(source_id=EID, source_type="code", snippet=SNIPPET, skill_tags=["redis"]))
    graph.add_chunk(EvidenceChunk(chunk_id=f"{EID}#chunk0", source_id=EID, text=SNIPPET))

    store_path = None
    if tmp_path is not None:
        store_path = tmp_path / "chroma"
    store = VectorStore(path=store_path, collection_name=f"pipe_{uuid.uuid4().hex[:8]}")
    emb = FakeEmbedder(dim=256)
    store.add(
        ids=[f"{EID}#chunk0"],
        embeddings=emb.embed([SNIPPET]),
        documents=[SNIPPET],
        metadatas=[{"source_id": EID, "source_type": "code", "skill_tags": "redis"}],
    )
    return graph, store, emb


def _jd_json():
    return json.dumps(
        {
            "title": "Senior Backend Engineer",
            "company": "Acme",
            "requirements": [{"requirement": "Redis and message queues", "priority": "must_have", "keywords": ["redis"]}],
        }
    )


def _good_bullet_sections():
    return json.dumps(
        {
            "sections": [
                {
                    "title": "Experience",
                    "entries": [
                        {
                            "entry_type": "job",
                            "title": "Backend Engineer",
                            "subtitle": "Acme",
                            "bullets": [{"text": SNIPPET, "evidence_ids": [EID]}],
                        }
                    ],
                }
            ]
        }
    )


def _client_with_jd(jd_json: str | None = None) -> MockLLMClient:
    client = MockLLMClient()
    client.register("jd_parser", [jd_json or _jd_json()])
    client.register("matcher_reformulation", [json.dumps({"queries": [SNIPPET]})])  # mid-range safety net
    client.register("bullet_generation", [_good_bullet_sections()])
    client.register("verifier", [json.dumps({"verdicts": [{"bullet_id": 0, "verdict": "pass", "reason": "supported"}]})])
    return client


def test_bounce_loop_drops_unverifiable_bullet():
    """Bullet 0 passes, bullet 1 fails -> one revision drops it -> clean resume."""
    graph, store, emb = _corpus()

    bad_draft = json.dumps(
        {
            "sections": [
                {
                    "title": "Experience",
                    "entries": [
                        {
                            "entry_type": "job",
                            "title": "Backend Engineer",
                            "subtitle": "Acme",
                            "bullets": [
                                {"text": SNIPPET, "evidence_ids": [EID]},
                                {"text": "Managed a team of 20 engineers", "evidence_ids": [EID]},
                            ],
                        }
                    ],
                }
            ]
        }
    )

    client = MockLLMClient()
    client.register("jd_parser", [_jd_json()])
    client.register("matcher_reformulation", [json.dumps({"queries": [SNIPPET]})])
    client.register(
        "bullet_generation",
        [
            bad_draft,
            _good_bullet_sections(),  # revision: the overclaim is gone
        ],
    )
    client.register(
        "verifier",
        [
            json.dumps(
                {
                    "verdicts": [
                        {"bullet_id": 0, "verdict": "pass", "reason": "supported"},
                        {"bullet_id": 1, "verdict": "fail", "reason": "managing a team is not in the cited evidence"},
                    ]
                }
            ),
            json.dumps({"verdicts": [{"bullet_id": 0, "verdict": "pass", "reason": "supported"}]}),
        ],
    )

    jd = parse_jd("Senior Backend Engineer @ Acme, Redis queues", client=client)
    matched = match_jd(jd, store, emb, client=client)
    build = build_verified_resume(jd, matched, ResumeHeader(name="Jake Ryan"), client=client, graph=graph)

    assert build.revisions == 1  # the bounce happened exactly once
    texts = [b.text for _, b in iter_bullets(build.resume)]
    assert texts == [SNIPPET]  # overclaim dropped
    assert all(b.verified for _, b in iter_bullets(build.resume))
    assert len(build.dropped) == 1
    assert "managing a team" in build.dropped[0].reason
    # the revision builder call carried the verifier's feedback
    feedback_calls = [c for c in client.calls if c[0] == "bullet_generation" and "REVISION FEEDBACK" in c[2]]
    assert len(feedback_calls) == 1
    assert "managing a team" in feedback_calls[0][2]


def test_no_revision_when_everything_passes():
    graph, store, emb = _corpus()
    client = _client_with_jd()
    jd = parse_jd("Senior Backend Engineer @ Acme, Redis queues", client=client)
    matched = match_jd(jd, store, emb, client=client)
    build = build_verified_resume(jd, matched, ResumeHeader(name="Jake Ryan"), client=client, graph=graph)
    assert build.revisions == 0
    assert build.dropped == []
    assert [b.text for _, b in iter_bullets(build.resume)] == [SNIPPET]


def test_capstone_build_verify_render_pdf(tmp_path):
    """The Phase 3 gate: verified Resume JSON renders to a real PDF."""
    graph, store, emb = _corpus()
    client = _client_with_jd()
    jd = parse_jd("Senior Backend Engineer @ Acme, Redis queues", client=client)
    matched = match_jd(jd, store, emb, client=client)
    build = build_verified_resume(jd, matched, ResumeHeader(name="Jake Ryan", contact={"email": "jake@x.com"}), client=client, graph=graph)

    assert build.resume.name == "Jake Ryan"
    assert build.resume.contact == {"email": "jake@x.com"}

    from resume_tailor.render.latex import render_resume

    pdf = render_resume(build.resume, tmp_path, jobname="verified")
    assert pdf.exists() and pdf.read_bytes()[:4] == b"%PDF"
    # verified bullets are in the tex with their citations intact
    tex = (tmp_path / "verified.tex").read_text(encoding="utf-8")
    assert "Jake Ryan" in tex
    assert "Redis-backed" in tex


def _write_corpus(tmp_path):
    """A tiny persisted corpus (Chroma + evidence_graph.json) in tmp_path."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    emb = FakeEmbedder(dim=256)
    store = VectorStore(path=corpus_dir / "chroma")  # default collection name
    store.add(
        ids=[f"{EID}#chunk0"],
        embeddings=emb.embed([SNIPPET]),
        documents=[SNIPPET],
        metadatas=[{"source_id": EID, "source_type": "code", "skill_tags": "redis"}],
    )
    graph = EvidenceGraph()
    graph.add_source(Evidence(source_id=EID, source_type="code", snippet=SNIPPET, skill_tags=["redis"]))
    graph.add_chunk(EvidenceChunk(chunk_id=f"{EID}#chunk0", source_id=EID, text=SNIPPET))
    (corpus_dir / "evidence_graph.json").write_text(graph.model_dump_json(), encoding="utf-8")
    return corpus_dir, emb


def test_run_full_end_to_end(tmp_path):
    """run_full: offline corpus dir -> parse -> match -> build -> verify -> PDF."""
    corpus_dir, emb = _write_corpus(tmp_path)
    client = _client_with_jd()
    out_dir = tmp_path / "out"
    result = run_full(
        "Senior Backend Engineer @ Acme, Redis queues",
        ResumeHeader(name="Jake Ryan", contact={"email": "jake@x.com"}),
        client=client,
        corpus_dir=corpus_dir,
        out_dir=out_dir,
        jobname="verified",
        embedder=emb,
    )
    assert result.pdf is not None and result.pdf.read_bytes()[:4] == b"%PDF"
    assert result.stats["matched"] == 1
    assert result.stats["requirements"] == 1
    assert result.stats["bullets_verified"] == 1
    assert result.stats["dropped_bullets"] == 0
    assert result.jd.title == "Senior Backend Engineer"
    assert result.build_result.resume.name == "Jake Ryan"
    # run_full defaults to hybrid: the mock JD's keyword "redis" lands in the
    # lexical pool at 0.2, beating the semantic hit at 0.213 -> best is lexical.
    assert result.stats["lexical_matches"] == 1
    assert result.stats["reformulations"] == 0  # ...and no reformulation was needed


def test_run_full_respects_explicit_semantic_only(tmp_path):
    """Passing an explicit MatchConfig overrides the hybrid default."""
    corpus_dir, emb = _write_corpus(tmp_path)
    client = _client_with_jd()
    result = run_full(
        "Senior Backend Engineer @ Acme, Redis queues",
        ResumeHeader(name="Jake Ryan"),
        client=client,
        corpus_dir=corpus_dir,
        out_dir=tmp_path / "out",
        jobname="verified",
        embedder=emb,
        match_config=MatchConfig(use_keywords=False),
    )
    assert result.stats["matched"] == 1  # still matched (semantic 0.213 < 0.62)
    assert result.stats["lexical_matches"] == 0  # but no lexical evidence


def test_run_full_hybrid_gap_stays_gap(tmp_path):
    """Hybrid never fabricates: a requirement with no keyword anywhere is still
    an honest gap, even with use_keywords=True."""
    corpus_dir, emb = _write_corpus(tmp_path)
    client = MockLLMClient()
    # "zzz qqq www" is the suite's disjoint-char query: semantically a hopeless
    # gap under FakeEmbedder (distance ~0.82), and its keyword "zzz" appears
    # nowhere in the corpus -> hybrid cannot rescue it either.
    client.register(
        "jd_parser",
        [
            json.dumps(
                {
                    "title": "Engineer",
                    "company": "Acme",
                    "requirements": [
                        {"requirement": "zzz qqq www", "priority": "nice_to_have", "keywords": ["zzz"]}
                    ],
                }
            )
        ],
    )
    client.register("bullet_generation", ['{"sections": []}'])
    # distance ~0.72 is mid-range -> reformulation runs; empty queries stop it
    # immediately (gap stays gap, no fabrication).
    client.register("matcher_reformulation", ['{"queries": []}'])
    result = run_full(
        "zzz qqq www",
        ResumeHeader(name="Jake Ryan"),
        client=client,
        corpus_dir=corpus_dir,
        out_dir=tmp_path / "out",
        jobname="verified",
        embedder=emb,
    )
    assert result.stats["matched"] == 0
    assert result.stats["gaps"] == 1
    assert result.stats["lexical_matches"] == 0


def test_run_full_draft_mode_skips_verification(tmp_path):
    """evidence_based=False: the builder draft is used as-is — no verification
    pass, no drops; every drafted bullet survives (marked unverified)."""
    corpus_dir, emb = _write_corpus(tmp_path)
    client = _client_with_jd()
    result = run_full(
        "Senior Backend Engineer @ Acme, Redis queues",
        ResumeHeader(name="Jake Ryan"),
        client=client,
        corpus_dir=corpus_dir,
        out_dir=tmp_path / "out",
        jobname="draft",
        embedder=emb,
        evidence_based=False,
    )
    assert result.stats["evidence_based"] is False
    assert result.stats["dropped_bullets"] == 0
    assert result.stats["revisions"] == 0
    assert result.build_result.verification.verdicts == []  # verifier never ran
    assert not [c for c in client.calls if c[0] == "verifier"]
    bullets = list(iter_bullets(result.build_result.resume))
    assert len(bullets) == 1
    assert bullets[0][1].text == SNIPPET
    assert bullets[0][1].verified is False  # draft: honest unverified marker
    assert result.pdf is not None and result.pdf.read_bytes()[:4] == b"%PDF"


def test_run_full_draft_vs_verified_mode_flags(tmp_path):
    """The two modes differ in the stats ledger: verified mode runs the bounce,
    draft mode skips it entirely."""
    corpus_dir, emb = _write_corpus(tmp_path)
    verified = run_full(
        "Senior Backend Engineer @ Acme, Redis queues",
        ResumeHeader(name="Jake Ryan"),
        client=_client_with_jd(),
        corpus_dir=corpus_dir,
        out_dir=tmp_path / "out",
        jobname="v",
        embedder=emb,
    )
    draft = run_full(
        "Senior Backend Engineer @ Acme, Redis queues",
        ResumeHeader(name="Jake Ryan"),
        client=_client_with_jd(),
        corpus_dir=corpus_dir,
        out_dir=tmp_path / "out",
        jobname="d",
        embedder=emb,
        evidence_based=False,
    )
    assert verified.stats["evidence_based"] is True
    assert verified.stats["bullets_verified"] == 1  # pass -> verified=True
    assert draft.stats["bullets_verified"] == 0  # draft bullets are unverified
    assert verified.build_result.verification.verdicts  # verifier ran
    assert draft.build_result.verification.verdicts == []


def test_run_full_empty_corpus_is_all_gaps():
    """Honest failure: no corpus -> every requirement is a gap, empty resume."""
    client = _client_with_jd()
    client.register("bullet_generation", ['{"sections": []}'])
    tmp = tempfile.mkdtemp()
    result = run_full(
        "Senior Backend Engineer @ Acme, Redis queues",
        ResumeHeader(name="Jake Ryan"),
        client=client,
        corpus_dir=tmp,  # empty dir -> empty store
        out_dir=tmp,
        jobname="verified",
        embedder=FakeEmbedder(dim=256),
    )
    assert result.stats["matched"] == 0
    assert result.stats["gaps"] == 1
    assert result.stats["bullets_verified"] == 0


def test_dropped_ledger_dedupes_across_rounds():
    """A bullet the builder fails to fix fails in BOTH rounds — the trace must
    list it once, not twice (cross-round dedup by claim)."""
    graph, store, emb = _corpus()

    unchanged_bad_draft = json.dumps(
        {
            "sections": [
                {
                    "title": "Experience",
                    "entries": [
                        {
                            "entry_type": "job",
                            "title": "Backend Engineer",
                            "subtitle": "Acme",
                            "bullets": [{"text": "Managed a team of 20 engineers", "evidence_ids": [EID]}],
                        }
                    ],
                }
            ]
        }
    )

    client = MockLLMClient()
    client.register("jd_parser", [_jd_json()])
    client.register("matcher_reformulation", [json.dumps({"queries": [SNIPPET]})])
    client.register("bullet_generation", [unchanged_bad_draft, unchanged_bad_draft])  # builder ignores feedback
    client.register(
        "verifier",
        [
            json.dumps({"verdicts": [{"bullet_id": 0, "verdict": "fail", "reason": "not in evidence"}]}),
            json.dumps({"verdicts": [{"bullet_id": 0, "verdict": "fail", "reason": "not in evidence"}]}),
        ],
    )

    jd = parse_jd("Senior Backend Engineer @ Acme, Redis queues", client=client)
    matched = match_jd(jd, store, emb, client=client)
    build = build_verified_resume(jd, matched, ResumeHeader(name="Jake Ryan"), client=client, graph=graph)

    assert build.revisions == 1  # bounce ran
    assert len(build.dropped) == 1  # same bullet failed twice -> listed once
    assert build.resume.sections == []  # nothing survived
    assert build.dropped[0].claim == "Managed a team of 20 engineers"
