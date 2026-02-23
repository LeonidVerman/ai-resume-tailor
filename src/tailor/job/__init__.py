from dataclasses import dataclass


@dataclass
class JobData:
    company: str
    job_title: str
    description: str
    source_url: str | None = None
