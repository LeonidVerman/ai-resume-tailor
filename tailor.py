import requests
import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from docx import Document

# ---------------------------
# Utilities
# ---------------------------

def get_job_description(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, "html.parser")
    return soup.get_text(separator="\n")


def read_docx(file_path):
    doc = Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs])


def save_doc(filename, text):
    doc = Document()

    for line in text.split("\n"):
        line = line.strip()

        if line.startswith("- "):
            doc.add_paragraph(line[2:], style="List Bullet")
        elif line:
            doc.add_paragraph(line)
        else:
            doc.add_paragraph("")

    doc.save(filename)


# ---------------------------
# OpenAI Setup
# ---------------------------

load_dotenv()
client = OpenAI()


# ---------------------------
# Step 1 — Extract Company & Role
# ---------------------------

def extract_metadata_ai(job_text):
    prompt = f"""
Extract the following from this job description:

1. Company name
2. Job title

Return JSON only:

{{
  "company": "...",
  "job_title": "..."
}}

JOB DESCRIPTION:
{job_text}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)

def extract_metadata_from_html(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, "html.parser")

    # Find JSON-LD script
    script_tag = soup.find("script", type="application/ld+json")

    if not script_tag:
        return {"company": "Unknown", "job_title": "Unknown"}

    data = json.loads(script_tag.string)

    company = data.get("hiringOrganization", {}).get("name", "Unknown")
    job_title = data.get("title", "Unknown")

    return {
        "company": company,
        "job_title": job_title
    }

# ---------------------------
# Step 2 — Tailor Resume + Cover
# ---------------------------

def tailor_documents(job_text, resume_template, cover_template, company, job_title):

    prompt = f"""
You are a senior technical recruiter and resume strategist.

Company: {company}
Role: {job_title}

JOB DESCRIPTION:
{job_text}

MASTER RESUME:
{resume_template}

MASTER COVER LETTER:
{cover_template}

Instructions:

1. Significantly tailor resume to match role.
2. Reorder bullets by relevance.
3. Strengthen impact statements.
4. Remove or reduce irrelevant emphasis.
5. Incorporate job keywords naturally.
6. Do NOT invent new technologies or roles.
7. Rewrite cover letter specifically for this company and role.
8. Mention company name and job title explicitly in cover letter.

Return JSON only:

{{
  "resume": "...",
  "cover_letter": "..."
}}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)


# ---------------------------
# Main
# ---------------------------

if __name__ == "__main__":

    job_url = input("Enter job URL (or press enter to paste text): ").strip()

    if job_url:
        job_text = get_job_description(job_url)
    else:
        print("Paste job description (press Enter twice to finish):")
        lines = []
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
        job_text = "\n".join(lines)

    resume_template = read_docx("templates/Leonid_Verman_Resume_Template.docx")
    cover_template = read_docx("templates/Leonid_Verman_Cover_Letter_Template.docx")

    print("Extracting company & role...")
    metadata = extract_metadata_from_html(job_text)
    company = metadata.get("company", "the company")
    job_title = metadata.get("job_title", "the role")

    print(f"Detected Company: {company}")
    print(f"Detected Role: {job_title}")

    print("Tailoring documents...")
    result = tailor_documents(job_text, resume_template, cover_template, company, job_title)

    os.makedirs("output", exist_ok=True)

    save_doc(f"output/{company}_Resume.docx", result["resume"])
    save_doc(f"output/{company}_CoverLetter.docx", result["cover_letter"])

    print("Documents generated successfully.")