"""Reusable sample Resume for Phase 0 demos and tests.

Deliberately mirrors Jake's Resume reference content so the rendered output can
be compared against the original template by eye.
"""
from __future__ import annotations

from resume_tailor.schemas import Resume, ResumeBullet, ResumeEntry, ResumeSection


def sample_resume() -> Resume:
    return Resume(
        name="Jake Ryan",
        contact={
            "phone": "123-456-7890",
            "email": "jake@su.edu",
            "linkedin": "linkedin.com/in/jake",
            "github": "github.com/jaker",
        },
        sections=[
            ResumeSection(
                title="Education",
                entries=[
                    ResumeEntry(
                        entry_type="education",
                        title="Southwestern University",
                        subtitle="Bachelor of Arts in Computer Science, Minor in Business",
                        location="Georgetown, TX",
                        dates="Aug. 2018 -- May 2021",
                        bullets=[
                            ResumeBullet(
                                text="Relevant Coursework: Data Structures, Algorithms, Database Systems",
                                evidence_ids=["edu:su#coursework"],
                                verified=True,
                            ),
                        ],
                    ),
                ],
            ),
            ResumeSection(
                title="Experience",
                entries=[
                    ResumeEntry(
                        entry_type="job",
                        title="Undergraduate Research Assistant",
                        subtitle="Texas A&M University",
                        location="College Station, TX",
                        dates="June 2020 -- Present",
                        bullets=[
                            ResumeBullet(
                                text="Developed a REST API using FastAPI and PostgreSQL to store data from learning management systems",
                                evidence_ids=["repo:backend-api#file:queue.py#L45"],
                                verified=True,
                            ),
                            ResumeBullet(
                                text="Developed a full-stack web application using Flask, React, PostgreSQL and Docker to analyze GitHub data",
                                evidence_ids=["repo:backend-api#file:app.py#L10"],
                                verified=True,
                            ),
                        ],
                    ),
                    ResumeEntry(
                        entry_type="job",
                        title="Information Technology Support Specialist",
                        subtitle="Southwestern University",
                        location="Georgetown, TX",
                        dates="Sep. 2018 -- Present",
                        bullets=[
                            ResumeBullet(
                                text="Maintained upkeep of computers, classroom equipment, and 200 printers across campus",
                                evidence_ids=["resume:old#L34"],
                                verified=True,
                            ),
                        ],
                    ),
                ],
            ),
            ResumeSection(
                title="Projects",
                entries=[
                    ResumeEntry(
                        entry_type="project",
                        title="Gitlytics",
                        subtitle="Python, Flask, React, PostgreSQL, Docker",
                        dates="June 2020 -- Present",
                        bullets=[
                            ResumeBullet(
                                text="Implemented GitHub OAuth to get data from user's repositories",
                                evidence_ids=["repo:gitlytics#file:auth.py#L20"],
                                verified=True,
                            ),
                            ResumeBullet(
                                text="Used Celery and Redis for asynchronous tasks",
                                evidence_ids=["repo:gitlytics#file:tasks.py#L7"],
                                verified=True,
                            ),
                        ],
                    ),
                ],
            ),
            ResumeSection(
                title="Technical Skills",
                bullets=[
                    ResumeBullet(
                        text="Languages: Java, Python, C/C++, SQL (Postgres), JavaScript, HTML/CSS, R",
                        evidence_ids=["resume:old#L51"],
                        verified=True,
                    ),
                    ResumeBullet(
                        text="Frameworks: React, Node.js, Flask, JUnit, WordPress, Material-UI, FastAPI",
                        evidence_ids=["resume:old#L52"],
                        verified=True,
                    ),
                    ResumeBullet(
                        text="Developer Tools: Git, Docker, TravisCI, Google Cloud Platform, VS Code",
                        evidence_ids=["resume:old#L53"],
                        verified=True,
                    ),
                    ResumeBullet(
                        text="Libraries: pandas, NumPy, Matplotlib",
                        evidence_ids=["resume:old#L54"],
                        verified=True,
                    ),
                ],
            ),
        ],
    )
