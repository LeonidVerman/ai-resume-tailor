import json
import os
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from docx import Document
from dotenv import load_dotenv
from openai import OpenAI
from playwright.sync_api import sync_playwright


# ---------------------------
# Utilities
# ---------------------------

def get_rendered_html(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(url, timeout=60000)
        page.wait_for_load_state("networkidle")

        html = page.content()
        browser.close()

    return html

def read_docx(file_path):
    doc = Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs])


def replace_paragraph_text(paragraph, new_text):
    """
    Replace text in a paragraph while preserving the formatting of the first run.
    """
    if not paragraph.runs:
        paragraph.text = new_text
        return

    # Store first run's formatting by keeping the run object
    first_run = paragraph.runs[0]

    # Clear text from all runs
    for run in paragraph.runs:
        run.text = ""

    # Set new text on first run (preserves its formatting)
    first_run.text = new_text


def save_doc_from_template(template_path, output_path, new_text):
    """
    Modify a copy of the template document, replacing text while preserving formatting.
    """
    doc = Document(template_path)

    # Parse new text into lines
    new_lines = [line for line in new_text.split("\n")]

    template_paras = list(doc.paragraphs)
    new_line_idx = 0

    for para in template_paras:
        if new_line_idx >= len(new_lines):
            # No more new lines - clear remaining paragraphs
            replace_paragraph_text(para, "")
            continue

        new_line = new_lines[new_line_idx]

        # Handle bullet points - strip the "- " prefix if present
        if new_line.strip().startswith("- "):
            new_line = new_line.strip()[2:]

        replace_paragraph_text(para, new_line)
        new_line_idx += 1

    # If there are more new lines than template paragraphs, append them
    # Use the style of the last paragraph as a fallback
    last_style = template_paras[-1].style if template_paras else None

    while new_line_idx < len(new_lines):
        new_line = new_lines[new_line_idx]

        if new_line.strip().startswith("- "):
            doc.add_paragraph(new_line.strip()[2:], style="List Bullet")
        elif new_line.strip():
            p = doc.add_paragraph(new_line)
            if last_style:
                p.style = last_style
        else:
            doc.add_paragraph("")

        new_line_idx += 1

    doc.save(output_path)


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


def extract_job_data_from_html(html):
    soup = BeautifulSoup(html, "html.parser")

    script_tags = soup.find_all("script", type="application/ld+json")

    for tag in script_tags:
        try:
            data = json.loads(tag.string)

            # Sometimes JSON-LD is a list
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "JobPosting":
                        return parse_jobposting(item)
            elif data.get("@type") == "JobPosting":
                return parse_jobposting(data)

        except Exception:
            continue

    return {
        "company": "Unknown",
        "job_title": "Unknown",
        "description": ""
    }


def parse_jobposting(data):
    company = data.get("hiringOrganization", {}).get("name", "Unknown")
    job_title = data.get("title", "Unknown")

    description_html = data.get("description", "")
    description_soup = BeautifulSoup(description_html, "html.parser")
    description_text = description_soup.get_text(separator="\n")

    return {
        "company": company,
        "job_title": job_title,
        "description": description_text
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
2. Maintain original experience order (reverse chronological).
3. Reorder bullet points within each role only.
4. Strengthen impact statements.
5. Remove or reduce irrelevant emphasis.
6. Incorporate job keywords naturally.
7. Do NOT invent new technologies or roles.
8. Rewrite cover letter specifically for this company and role.
9. Mention company name and job title explicitly in cover letter.

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
# Debug Autosave
# ---------------------------

def save_debug_data(company, job_title, job_description, llm_response):
    os.makedirs("tmp", exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_company = "".join(c if c.isalnum() or c in "_-" else "_" for c in company).strip("_")
    safe_title = "".join(c if c.isalnum() or c in "_-" else "_" for c in job_title).strip("_")
    filename = f"tmp/{safe_company}-{safe_title}-{timestamp}.json"

    data = {
        "company": company,
        "position": job_title,
        "job_description": job_description,
        "llm_response": llm_response,
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Debug data saved to {filename}")


# ---------------------------
# Main
# ---------------------------

if __name__ == "__main__":

    job_url = input("Enter job URL (or press enter to paste text): ").strip()

    if job_url:
        html = get_rendered_html(job_url)
        job_data = extract_job_data_from_html(html)
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

    company = job_data["company"]
    job_title = job_data["job_title"]
    job_text = job_data["description"]

    print(f"Detected Company: {company}")
    print(f"Detected Role: {job_title}")

    print("Tailoring documents...")
    result = tailor_documents(job_text, resume_template, cover_template, company, job_title)

    save_debug_data(company, job_title, job_text, result)

    os.makedirs("output", exist_ok=True)

    resume_template_path = "templates/Leonid_Verman_Resume_Template.docx"
    cover_template_path = "templates/Leonid_Verman_Cover_Letter_Template.docx"

    save_doc_from_template(resume_template_path, f"output/{company}_Resume.docx", result["resume"])
    save_doc_from_template(cover_template_path, f"output/{company}_CoverLetter.docx", result["cover_letter"])

    print("Documents generated successfully.")