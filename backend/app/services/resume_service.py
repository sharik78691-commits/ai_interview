"""Resume upload: validation, text extraction, heuristic parsing."""
import io
import re

from app.models.resume import ResumeData

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
MAX_BYTES = 10 * 1024 * 1024

SKILL_KEYWORDS = [
    "python", "java", "spring", "spring boot", "fastapi", "django", "flask",
    "react", "angular", "typescript", "javascript", "node", "node.js",
    "sql", "postgresql", "mysql", "mongodb", "aws", "azure", "gcp",
    "docker", "kubernetes", "redis", "kafka", "machine learning",
    "deep learning", "nlp", "tensorflow", "pytorch", "c++", "c#", "go",
    "rust", "git", "linux", "rest", "graphql", "html", "css",
]

SECTION_HEADERS = {
    "summary": ["summary", "objective", "profile"],
    "skills": ["skills", "technical skills", "tech stack"],
    "experience": ["experience", "work experience", "employment"],
    "projects": ["projects", "personal projects"],
    "education": ["education", "academic"],
    "certifications": ["certifications", "certificates", "licenses"],
}

# Below this many non-space characters, a PDF/DOCX almost certainly failed to
# parse (typically a scanned, image-only PDF) — better to ask for DOCX/TXT
# than to store a broken fragment as the user's resume.
_MIN_READABLE_CHARS = 200


def _readable_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _page_text(page, layout: bool = False) -> str:
    """Extract one PDF page's text, never raising (blank/image pages vary)."""
    try:
        if layout:
            return page.extract_text(extraction_mode="layout") or ""
        return page.extract_text() or ""
    except Exception:
        return ""


def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {k: [] for k in SECTION_HEADERS}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        low = line.lower().strip(" :#*-")
        matched = None
        for section, headers in SECTION_HEADERS.items():
            if low in headers:
                matched = section
                break
        if matched:
            current = matched
            continue
        if current and line:
            sections[current].append(line)
    return sections


class ResumeService:
    def validate_file(self, filename: str, size: int) -> None:
        ext = "." + (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type '{ext}'. Allowed: .pdf, .docx, .txt"
            )
        if size > MAX_BYTES:
            raise ValueError(
                f"File too large ({size} bytes). Max is {MAX_BYTES} bytes (10MB)."
            )
        if size == 0:
            raise ValueError("Uploaded file is empty.")

    async def extract_text(self, filename: str, content: bytes) -> str:
        """Async-compatible wrapper (kept for existing callers)."""
        return self.extract_text_sync(filename, content)

    def extract_text_sync(self, filename: str, content: bytes) -> str:
        """CPU-bound extraction. Call from a worker thread in async code."""
        ext = "." + (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type '{ext}'. Allowed: .pdf, .docx, .txt"
            )
        if not content or not content.strip():
            raise ValueError("Uploaded file is empty.")
        if ext == ".txt":
            try:
                text = content.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                text = content.decode("latin-1")
            text = text.strip()
            if not text:
                raise ValueError("Uploaded file is empty.")
            return text
        if ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            text = "\n".join(_page_text(page) for page in reader.pages).strip()
            # Plain extraction drops content on complex layouts — retry in
            # layout mode and keep whichever yields more real text.
            if _readable_len(text) < _MIN_READABLE_CHARS and len(reader.pages) > 0:
                layout = "\n".join(
                    _page_text(page, layout=True) for page in reader.pages
                ).strip()
                if _readable_len(layout) > _readable_len(text):
                    text = layout
            if _readable_len(text) < _MIN_READABLE_CHARS:
                raise ValueError(
                    "Could not read this PDF (it looks scanned or image-based). "
                    "Please upload the DOCX or TXT version of the resume instead."
                )
            return text
        if ext == ".docx":
            import docx
            from docx.oxml.table import CT_Tbl
            from docx.oxml.text.paragraph import CT_P
            from docx.table import Table
            from docx.text.paragraph import Paragraph

            doc = docx.Document(io.BytesIO(content))
            # Walk the body in document order. Plain python-docx extraction
            # only sees paragraphs, so table-based resumes (very common) lost
            # most of their text — tables are read row by row here.
            parts: list[str] = []
            for child in doc.element.body.iterchildren():
                if isinstance(child, CT_P):
                    line = Paragraph(child, doc).text.strip()
                    if line:
                        parts.append(line)
                elif isinstance(child, CT_Tbl):
                    for row in Table(child, doc).rows:
                        cells = [cell.text.strip() for cell in row.cells]
                        line = " | ".join(t for t in cells if t)
                        if line:
                            parts.append(line)
            text = "\n".join(parts).strip()
            if not text:
                raise ValueError("Could not extract text from DOCX.")
            return text
        raise ValueError(f"Unsupported file type '{ext}'.")

    def parse_resume(self, text: str) -> ResumeData:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Resume text is empty.")
        lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]

        # Name: first non-empty line under 60 chars, strip emails/phones.
        name = ""
        for ln in lines[:5]:
            candidate = re.sub(r"[\w.%-]+@[\w.-]+\.\w+", "", ln).strip(" |,-")
            candidate = re.sub(r"\+?[\d\s().-]{7,}", "", candidate).strip(" |,-")
            if candidate and len(candidate) <= 60 and not re.search(r"\.(com|io|dev)", candidate, re.I):
                name = candidate
                break
        if not name:
            name = lines[0][:60] if lines else "Candidate"

        sections = _split_sections(cleaned)

        # Skills: keyword match over whole text (preserve order, dedupe).
        low_full = cleaned.lower()
        skills = [s for s in SKILL_KEYWORDS if s.lower() in low_full]
        # Also add comma-separated tokens from skills section.
        for ln in sections["skills"]:
            for token in re.split(r"[,|/•·;]", ln):
                t = token.strip(" -*#")
                if 1 < len(t) <= 40 and t.lower() not in [s.lower() for s in skills]:
                    if re.match(r"^[\w+#. ]+$", t):
                        skills.append(t)
        skills = skills[:30]

        exp_pattern = re.compile(
            r"(\b(19|20)\d{2}\b|\byears?\b|engineer|developer|manager|analyst|intern|company|inc\b|\bltd\b)",
            re.I,
        )
        experience = list(sections["experience"])
        if not experience:
            for ln in lines:
                if exp_pattern.search(ln) and len(ln) > 10:
                    experience.append(ln)
                if len(experience) >= 10:
                    break

        projects = list(sections["projects"])
        education = list(sections["education"])
        certifications = list(sections["certifications"])
        summary = " ".join(sections["summary"])[:1000]

        return ResumeData(
            name=name,
            summary=summary,
            skills=skills,
            experience=experience[:20],
            projects=projects[:20],
            education=education[:10],
            certifications=certifications[:10],
            raw_text=cleaned[:20000],
        )
