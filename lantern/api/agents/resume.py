"""
AUTO-RESUME PDF GENERATOR
Uses fit-gap analysis to generate a tailored resume per matched role.
Reorders experience, injects JD keywords, renders as ATS-safe PDF.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from core import llm

logger = logging.getLogger("lantern.resume")

TAILOR_PROMPT = """You are a resume optimization expert. Given a candidate profile and a job listing with fit-gap analysis, generate a tailored professional summary and list of prioritized bullet points.

CANDIDATE PROFILE:
{profile}

TARGET ROLE:
Title: {title}
Company: {company}
Description: {description}

FIT-GAP ANALYSIS:
Matched skills: {matched_skills}
Gaps: {gaps}
Talking points: {talking_points}

Generate a JSON response with:
{{
  "summary": "<3-4 sentence professional summary tailored to this specific role, highlighting the most relevant experience>",
  "keywords": ["<8-12 keywords from the JD to weave into the resume>"],
  "bullets": [
    "<rewritten experience bullet emphasizing relevance to this role>",
    "<another bullet>",
    "<6-8 total bullets, ordered by relevance to this role>"
  ],
  "cover_note": "<2-3 sentence personalized note explaining why this role is a strong fit>"
}}

Rules:
- Do not fabricate experience. Only reframe and reorder existing profile points.
- Front-load the most relevant experience.
- Use active verbs and quantify impact where the profile data supports it.
- The summary should feel written for THIS specific role, not generic.
"""


class ResumeGenerator:
    """Generates tailored PDF resumes for matched roles."""

    def __init__(self, config: dict):
        self.name = config.get("name", "Candidate")
        self.email = config.get("email", "")
        self.profile = config.get("profile", "")
        self.output_dir = Path(config.get("output_dir", "data/resumes"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tailor(self, fit_gap: dict) -> dict:
        """Use LLM to generate tailored resume content for a specific role."""
        prompt = TAILOR_PROMPT.format(
            profile=self.profile,
            title=fit_gap.get("title", ""),
            company=fit_gap.get("company", ""),
            description=fit_gap.get("description", "N/A"),
            matched_skills=", ".join(fit_gap.get("matched_skills", [])),
            gaps=", ".join(g.get("skill", "") for g in fit_gap.get("gaps", [])),
            talking_points=" | ".join(fit_gap.get("talking_points", [])),
        )

        result = llm.query_json(prompt, task="analyze")
        if result.get("_parse_error"):
            logger.warning("Failed to generate tailored content for %s @ %s",
                         fit_gap.get("title"), fit_gap.get("company"))
            return None
        return result

    def render_html(self, tailored: dict, fit_gap: dict) -> str:
        """Render tailored resume as clean, ATS-safe HTML."""
        company = fit_gap.get("company", "Company")
        title = fit_gap.get("title", "Role")
        match_pct = fit_gap.get("match_percentage", 0)

        summary = tailored.get("summary", "")
        bullets = tailored.get("bullets", [])
        keywords = tailored.get("keywords", [])
        cover = tailored.get("cover_note", "")

        # Clean company name for filename
        safe_company = re.sub(r"[^\w\s-]", "", company).strip().replace(" ", "_")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@400;600;700&family=Source+Sans+3:wght@300;400;500;600&display=swap');

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    font-family: 'Source Sans 3', 'Helvetica Neue', Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #1a1a1a;
    max-width: 8.5in;
    margin: 0 auto;
    padding: 0.6in 0.7in;
  }}

  .header {{
    border-bottom: 2px solid #1a1a1a;
    padding-bottom: 12px;
    margin-bottom: 16px;
  }}

  .name {{
    font-family: 'Source Serif 4', Georgia, serif;
    font-size: 22pt;
    font-weight: 700;
    letter-spacing: -0.5px;
  }}

  .contact {{
    font-size: 9.5pt;
    color: #555;
    margin-top: 4px;
  }}

  .tailored-for {{
    font-size: 8.5pt;
    color: #888;
    margin-top: 6px;
    font-style: italic;
  }}

  .section-title {{
    font-family: 'Source Serif 4', Georgia, serif;
    font-size: 11pt;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    border-bottom: 1px solid #ccc;
    padding-bottom: 3px;
    margin: 18px 0 10px;
    color: #1a1a1a;
  }}

  .summary {{
    font-size: 10.5pt;
    line-height: 1.6;
    color: #333;
    margin-bottom: 4px;
  }}

  .bullet {{
    margin-bottom: 6px;
    padding-left: 16px;
    position: relative;
    font-size: 10.5pt;
    line-height: 1.5;
  }}

  .bullet::before {{
    content: "\\2022";
    position: absolute;
    left: 0;
    color: #1a1a1a;
  }}

  .keywords {{
    font-size: 9pt;
    color: #666;
    margin-top: 12px;
    line-height: 1.6;
  }}

  .keyword {{
    display: inline-block;
    background: #f0efec;
    padding: 2px 8px;
    border-radius: 2px;
    margin: 2px 3px 2px 0;
    font-size: 8.5pt;
  }}

  .cover {{
    margin-top: 20px;
    padding: 12px 16px;
    background: #faf9f7;
    border-left: 3px solid #c44d2a;
    font-size: 10pt;
    line-height: 1.6;
    color: #444;
  }}

  .match-badge {{
    display: inline-block;
    background: {'#e8f0e8' if match_pct >= 75 else '#fef3c7'};
    color: {'#2d5a2d' if match_pct >= 75 else '#92400e'};
    font-size: 8.5pt;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 2px;
    margin-left: 8px;
  }}

  @media print {{
    body {{ padding: 0.5in; }}
    .cover {{ break-inside: avoid; }}
  }}
</style>
</head>
<body>

<div class="header">
  <div class="name">{self.name}</div>
  <div class="contact">{self.email}</div>
  <div class="tailored-for">
    Tailored for: {title} at {company}
    <span class="match-badge">{match_pct}% match</span>
  </div>
</div>

<div class="section-title">Professional Summary</div>
<div class="summary">{summary}</div>

<div class="section-title">Relevant Experience</div>
{''.join(f'<div class="bullet">{b}</div>' for b in bullets)}

<div class="section-title">Alignment</div>
<div class="keywords">
  {''.join(f'<span class="keyword">{k}</span>' for k in keywords)}
</div>

<div class="cover">{cover}</div>

</body>
</html>"""
        return html

    def generate_pdf(self, fit_gap: dict) -> Path | None:
        """Generate a tailored PDF resume for a matched role."""
        title = fit_gap.get("title", "Role")
        company = fit_gap.get("company", "Company")

        logger.info("Generating tailored resume for: %s @ %s", title, company)

        # Get tailored content from LLM
        tailored = self.tailor(fit_gap)
        if not tailored:
            return None

        # Render HTML
        html = self.render_html(tailored, fit_gap)

        # Save HTML version
        safe_name = re.sub(r"[^\w\s-]", "", f"{company}_{title}").strip().replace(" ", "_")[:80]
        html_path = self.output_dir / f"{safe_name}.html"
        html_path.write_text(html, encoding="utf-8")

        # Try PDF conversion. We try two backends, in order, and fall
        # through to the HTML file if both are unavailable.
        #
        # Backend 1: weasyprint (fast, small, but weak on modern CSS).
        # Backend 2: Playwright headless Chromium (heavy dep but prints
        #            exactly what the browser renders -- best for complex
        #            layouts with flexbox, grid, web fonts).
        pdf_path = self.output_dir / f"{safe_name}.pdf"

        # Backend 1: weasyprint
        try:
            import weasyprint
            weasyprint.HTML(string=html).write_pdf(str(pdf_path))
            logger.info("PDF saved via weasyprint: %s", pdf_path)
            return pdf_path
        except ImportError:
            logger.info("weasyprint not available, trying Playwright PDF backend")
        except Exception as e:
            logger.warning("weasyprint failed (%s), trying Playwright PDF backend", e)

        # Backend 2: Playwright. render_html_to_pdf returns False if
        # Playwright isn't installed -- treat that the same as weasyprint
        # being missing and fall back to the HTML file.
        try:
            from agents.playwright_runner import render_html_to_pdf
            if render_html_to_pdf(html, str(pdf_path)):
                logger.info("PDF saved via Playwright: %s", pdf_path)
                return pdf_path
        except Exception as e:
            logger.warning("Playwright PDF backend failed: %s", e)

        logger.info("No PDF backend available. HTML resume saved: %s", html_path)
        return html_path

    def run(self, fit_gap_reports: list) -> list[dict]:
        """Generate tailored resumes for all fit-gap reports."""
        results = []
        if not fit_gap_reports:
            return results

        logger.info("Generating %d tailored resumes", len(fit_gap_reports))

        for i, report in enumerate(fit_gap_reports):
            if report.get("error"):
                continue

            logger.info("Resume %d/%d: %s @ %s", i + 1, len(fit_gap_reports),
                       report.get("title"), report.get("company"))

            path = self.generate_pdf(report)
            results.append({
                "title": report.get("title"),
                "company": report.get("company"),
                "path": str(path) if path else None,
                "match_percentage": report.get("match_percentage"),
            })

        logger.info("Generated %d tailored resumes in %s", len(results), self.output_dir)
        return results
