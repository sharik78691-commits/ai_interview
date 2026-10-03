"""Resume service tests."""
import pytest

from app.services.resume_service import ResumeService

SAMPLE = """Jane Doe
Senior Python Developer

SUMMARY
Experienced backend engineer with 5 years building APIs.

SKILLS
Python, FastAPI, Docker, AWS, SQL

EXPERIENCE
Senior Backend Engineer at Acme Inc 2021-2024
Built REST APIs serving 1M requests per day.

PROJECTS
Realtime dashboard with React and FastAPI.

EDUCATION
B.Sc Computer Science, State University 2016-2020

CERTIFICATIONS
AWS Certified Solutions Architect
"""


@pytest.fixture
def service() -> ResumeService:
    return ResumeService()


async def test_extract_txt(service: ResumeService) -> None:
    text = await service.extract_text("resume.txt", SAMPLE.encode())
    assert "Jane Doe" in text


async def test_parse_skills(service: ResumeService) -> None:
    resume = service.parse_resume(SAMPLE)
    lowered = [s.lower() for s in resume.skills]
    assert "python" in lowered
    assert resume.name != ""
    assert len(resume.experience) >= 1


async def test_reject_exe(service: ResumeService) -> None:
    with pytest.raises(ValueError):
        await service.extract_text("malware.exe", b"fake content")
    with pytest.raises(ValueError):
        service.validate_file("malware.exe", 100)
