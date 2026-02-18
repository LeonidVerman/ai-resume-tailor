import json
import os
from copy import deepcopy
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from docx import Document
from docx.text.paragraph import Paragraph as DocxParagraph
from dotenv import load_dotenv
from openai import OpenAI
from playwright.sync_api import sync_playwright

# XML namespace shorthand
_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


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


def _strip_section_break(para):
    """Remove any section break (w:sectPr) embedded in a paragraph's w:pPr.

    Templates sometimes carry a mid-document section break in a paragraph's
    pPr (visible as an unwanted page break).  We always remove it when we
    touch a paragraph so it doesn't bleed into the output.
    """
    pPr = para._p.find(f'{{{_W}}}pPr')
    if pPr is not None:
        sectPr = pPr.find(f'{{{_W}}}sectPr')
        if sectPr is not None:
            pPr.remove(sectPr)


def replace_paragraph_text(paragraph, new_text):
    """
    Replace text in a paragraph while preserving the formatting of the first run.
    Also strips any embedded section break so the paragraph never forces a page break.
    """
    _strip_section_break(paragraph)

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


def _clear_para(para):
    """Clear a template paragraph that has no matching LLM content.

    Beyond setting text to "", also removes list-numbering (w:numPr) and any
    embedded section break so the empty paragraph doesn't render as a
    visible bullet or trigger an unwanted page break.
    """
    replace_paragraph_text(para, '')
    pPr = para._p.find(f'{{{_W}}}pPr')
    if pPr is not None:
        numPr = pPr.find(f'{{{_W}}}numPr')
        if numPr is not None:
            pPr.remove(numPr)


def insert_paragraph_after(ref_para, text, style_source=None):
    """
    Clone style_source's XML (or ref_para's if style_source is None), set text,
    insert the clone immediately after ref_para, and return it as a Paragraph.
    """
    clone_from = style_source if style_source is not None else ref_para
    new_p = deepcopy(clone_from._p)
    ref_para._p.addnext(new_p)
    new_para = DocxParagraph(new_p, ref_para._p.getparent())
    replace_paragraph_text(new_para, text)
    return new_para


def _apply_groups(para_list, text, doc):
    """
    Apply LLM text to a list of template paragraphs using blank-line grouping.
    Groups are separated by empty paragraphs in the template and blank lines in text.
    """
    llm_groups = [[]]
    for line in text.split("\n"):
        if not line.strip():
            llm_groups.append([])
        else:
            llm_groups[-1].append(line)

    template_groups = [[]]
    template_sep_paras = []
    for para in para_list:
        if not para.text.strip():
            template_groups.append([])
            template_sep_paras.append(para)
        else:
            template_groups[-1].append(para)

    global_last_para = None

    for i in range(max(len(template_groups), len(llm_groups))):
        template_group = template_groups[i] if i < len(template_groups) else []
        llm_group = llm_groups[i] if i < len(llm_groups) else []

        group_last_para = None

        for j, llm_line in enumerate(llm_group):
            line_text = llm_line.strip()
            if line_text.startswith("- "):
                line_text = line_text[2:]

            if j < len(template_group):
                replace_paragraph_text(template_group[j], line_text)
                group_last_para = template_group[j]
            else:
                anchor = group_last_para or global_last_para
                if anchor is not None:
                    group_last_para = insert_paragraph_after(anchor, line_text)
                else:
                    group_last_para = doc.add_paragraph(line_text)

        for j in range(len(llm_group), len(template_group)):
            _clear_para(template_group[j])

        if group_last_para:
            global_last_para = group_last_para
        elif template_group:
            global_last_para = template_group[-1]

        if i < len(template_sep_paras):
            global_last_para = template_sep_paras[i]


# Section headers that terminate the Experience block in LLM output
_SECTION_HEADERS = {'Education', 'Technical Skills', 'Skills', 'Certifications',
                    'Projects', 'Publications', 'Volunteer', 'Awards', 'References'}


def save_doc_from_template(template_path, output_path, new_text):
    """
    Modify a copy of the template document, replacing text while preserving formatting.

    For the Experience section, entries are matched individually by their index so that
    extra bullets added by the LLM are inserted with the correct List Paragraph style,
    not cloned from whatever paragraph happens to follow in the template.

    All other sections use blank-line grouping (same as the original algorithm).
    """
    doc = Document(template_path)
    paras = doc.paragraphs

    def is_exp_entry_header(p):
        """Experience entry headers use Normal style with bold text and contain '|'."""
        return (p.style.name == 'Normal' and bool(p.text.strip())
                and '|' in p.text
                and any(r.bold for r in p.runs))

    # --- Find Experience section boundaries in the template ---
    exp_h2_idx = None
    post_exp_h2_idx = None
    for i, p in enumerate(paras):
        if p.style.name == 'Heading 2' and 'experience' in p.text.lower():
            exp_h2_idx = i
        elif exp_h2_idx is not None and p.style.name == 'Heading 2' and post_exp_h2_idx is None:
            post_exp_h2_idx = i
    if post_exp_h2_idx is None:
        post_exp_h2_idx = len(paras)

    # --- Find Experience section boundaries in LLM text ---
    llm_lines = new_text.split('\n')
    llm_exp_idx = None
    llm_post_exp_idx = None
    for i, line in enumerate(llm_lines):
        s = line.strip()
        if s == 'Experience':
            llm_exp_idx = i
        elif llm_exp_idx is not None and s in _SECTION_HEADERS and llm_post_exp_idx is None:
            llm_post_exp_idx = i
    if llm_post_exp_idx is None:
        llm_post_exp_idx = len(llm_lines)

    # --- Fall back to the original full-document algorithm if sections not found ---
    if exp_h2_idx is None or llm_exp_idx is None:
        _apply_groups(paras, new_text, doc)
        doc.save(output_path)
        return

    # --- Pre-experience section ---
    # Skip name + contact paragraphs (before the first Heading 2) entirely — they
    # contain hyperlinks whose XML persists even after replace_paragraph_text, which
    # causes email/LinkedIn to appear duplicated.  Only update the sections that
    # follow (Professional Summary and onwards).
    first_h2_idx = next((i for i, p in enumerate(paras)
                         if p.style.name == 'Heading 2'), exp_h2_idx)
    llm_first_h2_idx = next((i for i, line in enumerate(llm_lines)
                              if line.strip() and
                              line.strip() == paras[first_h2_idx].text.strip()),
                             0)
    _apply_groups(paras[first_h2_idx:exp_h2_idx],
                  '\n'.join(llm_lines[llm_first_h2_idx:llm_exp_idx]), doc)

    # --- "Experience" heading ---
    replace_paragraph_text(paras[exp_h2_idx], llm_lines[llm_exp_idx].strip())

    # --- Parse template experience entries ---
    # Each entry starts at a Normal+bold paragraph and ends just before the next one.
    tmpl_entries = []
    cur = None
    for i in range(exp_h2_idx + 1, post_exp_h2_idx):
        p = paras[i]
        if is_exp_entry_header(p):
            if cur is not None:
                tmpl_entries.append(cur)
            cur = [p]
        elif cur is not None:
            cur.append(p)  # date, bullets, and any blanks within the entry
    if cur is not None:
        tmpl_entries.append(cur)

    # --- Parse LLM experience entries ---
    def is_llm_exp_hdr(line):
        s = line.strip()
        return bool(s) and '|' in s and not s.startswith('-')

    llm_entries = []
    cur = None
    for line in llm_lines[llm_exp_idx + 1:llm_post_exp_idx]:
        s = line.strip()
        if not s:
            continue  # skip blank lines between entries
        if is_llm_exp_hdr(line):
            if cur is not None:
                llm_entries.append(cur)
            cur = [s]
        elif cur is not None:
            cur.append(s[2:] if s.startswith('- ') else s)
    if cur is not None:
        llm_entries.append(cur)

    # --- Match and apply experience entries by index ---
    for idx, tmpl_entry in enumerate(tmpl_entries):
        if idx >= len(llm_entries):
            for p in tmpl_entry:
                _clear_para(p)
            continue

        llm_entry = llm_entries[idx]

        # Find a bullet paragraph to use as the style source for any new bullets
        bullet_para = next((p for p in tmpl_entry if p.style.name == 'List Paragraph'), None)

        # Replace the experience header (keep its blue/bold formatting)
        replace_paragraph_text(tmpl_entry[0], llm_entry[0])
        last_para = tmpl_entry[0]

        content_paras = tmpl_entry[1:]   # date + bullets in template
        content_lines = llm_entry[1:]    # date + bullets from LLM

        # Some templates split a header across two paragraphs (e.g. a long company
        # name wraps to a second Normal+bold line).  The LLM always emits a single
        # header line, so clear those continuation paragraphs before mapping content.
        skip = 0
        for p in content_paras:
            if p.style.name == 'Normal' and any(r.bold for r in p.runs):
                _clear_para(p)
                skip += 1
            else:
                break
        content_paras = content_paras[skip:]

        for j, text in enumerate(content_lines):
            if j < len(content_paras):
                replace_paragraph_text(content_paras[j], text)
                last_para = content_paras[j]
            else:
                # Extra bullet: clone from bullet_para to get List Paragraph style
                last_para = insert_paragraph_after(last_para, text,
                                                   style_source=bullet_para)

        # Clear leftover template paragraphs that the LLM didn't fill
        for j in range(len(content_lines), len(content_paras)):
            _clear_para(content_paras[j])

    # --- Post-experience section ---
    _apply_groups(paras[post_exp_h2_idx:], '\n'.join(llm_lines[llm_post_exp_idx:]), doc)

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