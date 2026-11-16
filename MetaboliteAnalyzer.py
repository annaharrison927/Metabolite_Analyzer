import requests
import xml.etree.ElementTree as ElementTree
import os
import time
import random
import pandas as pd
import tempfile
import re
import csv
import io
from collections.abc import Iterable
from typing import List, Tuple
from dotenv import load_dotenv
from requests import Response
from enum import Enum, auto
from markdown_pdf import MarkdownPdf, Section
from MyAI import MyAI
from urllib.parse import urlencode

# --- Configuration & Setup ---
NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
HEADERS = {'Content-Type': 'application/json'}
MAX_ARTICLES = 100
AMINO_ACIDS = {'Ala', 'Arg', 'Asn', 'Asp', 'Cys',
               'Glu', 'Gln', 'Gly', 'His', 'Ile',
               'Leu', 'Lys', 'Met', 'Phe', 'Pro',
               'Ser', 'Thr', 'Trp', 'Tyr', 'Val'}

load_dotenv()
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")

model_name = os.environ.get("OPENAI_MODEL", "gpt-6-luna")

# Standard API rates in USD per million tokens, checked against the published
# OpenAI pricing page on 2026-11-16. Update this table when rates change.
MODEL_PRICING = {
    "gpt-6-luna": {
        "input_usd_per_million_tokens": 0.05,
        "output_usd_per_million_tokens": 0.25,
        "effective_date": "2026-11-16",
    }
}

system_prompt = (
    "You are a biomedical literature assistant specializing in metabolite research. "
    "Synthesize the provided PubMed abstracts into a concise, objective summary of the "
    "biological function of each metabolite. Treat the abstracts as the evidence base; "
    "do not present a reported association as proof of causation or a finding in one species "
    "as established in all species.\n\n"
    "Return the fields required by the response schema. The summary must be exactly one "
    "concise sentence with no heading or other commentary. Summarize the most relevant "
    "metabolite functions supported by the abstracts.\n\n"
    "Use status complete when the abstracts support a metabolite function relevant to the "
    "search focus (or a clear biological function when no focus is provided). Use status "
    "no_relevant_results when abstracts were retrieved but do not support a relevant function.\n"
    "Include one to three plain-text PMID citations only when they directly support a "
    "function stated in the summary, using this format: [PMID: 1234567].\n\n"
    "- Cite only abstracts that directly support a relevant function. Do not cite abstracts "
    "merely because they were provided, and never list PMIDs without an associated claim.\n"
    "- If multiple abstracts support a relevant function, use a separate citation for each source "
    "(e.g., [PMID: 27090191], [PMID: 2382591]).\n"
    "- If status is no_relevant_results, say briefly in the summary that the abstracts do not "
    "support a function relevant to the search focus and include no citations.\n"
    "### EVIDENCE AND STYLE RULES ###\n"
    "- Synthesize the main relevant themes across the abstracts rather than listing "
    "individual findings or abstracts.\n"
    "- If a search focus or keywords are provided, prioritize relevant findings only "
    "when supported by the abstracts. If they do not address the focus, state this "
    "concisely; do not infer the requested property from the search terms alone.\n"
    "- Never invent or guess a PMID or other detail.\n"
    "- If findings are mixed or context-dependent, convey the key qualification briefly "
    "within the sentence.\n"
    "- If the abstracts do not support a clear biological role or finding, say so in one "
    "sentence rather than inventing a conclusion.\n"
    "- Do not infer that a metabolite has no effect merely because the provided abstracts "
    "do not report one.\n"
    "- Be concise and factual. Omit introductory or closing commentary."
)

my_ai = MyAI(model_name=model_name,system_prompt=system_prompt)

class Method(Enum):
    SEARCH = auto()
    FETCH = auto()

def exponential_backoff_request(url: str, method: str = "GET", payload: dict = None, max_retries: int = 5) -> (
        requests.Response | None):
    """
        Makes an HTTP request with exponential backoff for handling rate limits (429) and server errors.
    """
    last_exception = None
    for attempt in range(max_retries):
        try:
            if method == "GET":
                response = requests.get(url)
            else:
                response = requests.post(url, headers=HEADERS, json=payload)

            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response

            if response.status_code == 429:
                print(f"RATE LIMIT HIT (429) on attempt {attempt + 1}. Retrying...")
            else:  # 5xx errors
                print(f"Server Error ({response.status_code}) on attempt {attempt + 1}. Retrying...")

            delay = (2 ** attempt) + random.uniform(0, 1)
            print(f"Waiting for {delay:.2f} seconds before retry.")
            time.sleep(delay)

        except requests.exceptions.RequestException as e:
            last_exception = e
            print(f"Request failed: {e}. Retrying...")
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)


    raise RuntimeError(f"Failed to retrieve data after {max_retries} attempts.")

def get_metabolites() -> List[str]:
    metabolite_file = pd.read_csv("metabolite_list_small.csv")
    metabolite_df = pd.DataFrame(metabolite_file)
    return metabolite_df['Metabolites'].tolist()

def build_pubmed_query(metabolite: str, keyword: str = "") -> str:
    """Build a PubMed query for a metabolite and optional keywords."""

    def as_title_abstract_phrase(term: str) -> str:
        # Remove surrounding quotes and any internal quotes so the term stays
        # a single phrase in the generated query.
        phrase = term.strip().strip('"').replace('"', "")
        return f'"{phrase}"[tiab]'

    metabolite_term = as_title_abstract_phrase(metabolite)
    keywords = [
        term.strip()
        for term in re.split(r"[,;]", keyword or "")
        if term.strip()
    ]

    if not keywords:
        return metabolite_term

    keyword_terms = " OR ".join(
        as_title_abstract_phrase(term)
        for term in keywords
    )
    return f"({metabolite_term}) AND ({keyword_terms})"

def create_url(
    query_value: str,
    keyword: str = "",
    method: Method = Method.SEARCH,
) -> str:
    """Create an ESearch or EFetch URL for PubMed."""

    if method == Method.FETCH:
        params = {
            "db": "pubmed",
            "id": query_value,
            "retmode": "xml",
            "rettype": "abstract",
        }
        endpoint = "efetch.fcgi"
    else:
        params = {
            "db": "pubmed",
            "term": build_pubmed_query(query_value, keyword),
            "retmode": "json",
            "retmax": MAX_ARTICLES,
            "sort": "relevance",
        }
        endpoint = "esearch.fcgi"

    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY

    return f"{NCBI_BASE_URL}{endpoint}?{urlencode(params)}"

def is_peptide(metabolite: str) -> bool:
    return all(part in AMINO_ACIDS
               for part in metabolite.split("-"))

def normalize_peptide_notation(abstract:str) -> str:
    normalized = abstract
    normalized = normalized.replace("·", "-")
    normalized = normalized.replace("–", "-")
    normalized = normalized.replace("—", "-")
    normalized = normalized.replace("−", "-")
    return normalized

# Check if abstracts contain peptide of interest
def is_valid_abstract(abstract: str, peptide: str) -> bool:
    parts = abstract.split(peptide)
    beg = parts[0]
    end = parts[-1]

    if len(parts) == 2:
        if (beg == "" or beg[-1]) != "-" and (end == "" or end[0] != "-"):
            return True
        return False
    else:
        for i in range(len(parts)-1):
            front = parts[i]
            back = parts[i+1]
            if front[-1] != "-" and back[0] != "-":
                return True
        return False

def build_abstract_corpus(response: Response, metabolite:str) -> str:
    """
    Extracts info from each article to create a string of combined abstracts
    :param metabolite: str
    :param response: Response
    :return: str
    """
    root = ElementTree.fromstring(response.text)
    combined_abstract_text = ""
    seen_titles = set()

    for article in root.findall('.//PubmedArticle'):
        pmid_el = article.find(".//PMID")
        article_pmid = pmid_el.text if pmid_el is not None else "UnknownPMID"

        title_el = article.find(".//ArticleTitle")
        title = title_el.text if title_el is not None else "No Title"

        if title in seen_titles:
            continue

        seen_titles.add(title)

        author_el = article.find(".//Author/LastName")
        author = author_el.text if author_el is not None else "Unknown"

        abstract_text = ""
        abstract_elements = article.findall('.//AbstractText')
        peptide = is_peptide(metabolite)
        for element in abstract_elements:
            if element.text:
                abstract_text += element.text + " "
        if peptide:
            normalized_abstract = normalize_peptide_notation(abstract_text)
            if not is_valid_abstract(normalized_abstract, metabolite):
                continue
        if abstract_text:
            combined_abstract_text += f"\n\n--- ARTICLE PMID {article_pmid} | AUTHOR: {author}\nTITLE: {title}---\n{abstract_text.strip()}"

    return combined_abstract_text

def fetch_abstracts(metabolite: str, keyword: str) -> Tuple[str, List[str]] | None:
    """
    Executes ESearch and EFetch to retrieve abstracts for the top MAX_ARTICLES results.
    Returns a single concatenated abstract string and a list of PMIDs.
    """
    esearch_url = create_url(metabolite, keyword, Method.SEARCH)
    response = exponential_backoff_request(esearch_url)
    if not response:
        return "", []

    search_data = response.json()
    pmids = search_data['esearchresult']['idlist']
    if not pmids:
        return "", []

    pmid_list_str = ",".join(pmids)
    efetch_url = create_url(pmid_list_str, keyword, Method.FETCH)
    response = exponential_backoff_request(efetch_url)
    if not response:
        return "", []

    combined_abstract_text = build_abstract_corpus(response, metabolite)
    included_pmids = re.findall(r"--- ARTICLE PMID (\d+)", combined_abstract_text)
    return combined_abstract_text.strip(), included_pmids

def analyze_abstract(combined_abstracts: str, task: str) -> dict:
    input_text = task + combined_abstracts
    response = my_ai.generate_response(input_text=input_text)

    if not isinstance(response, dict):
        raise ValueError("Model response must be a JSON object")

    status = response.get("status")
    summary = response.get("summary")
    allowed_statuses = {"complete", "no_relevant_results"}
    if status not in allowed_statuses:
        raise ValueError(f"Model returned an unsupported status: {status!r}")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Model response did not include a summary")

    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    normalized_usage = {
        key: usage.get(key) if isinstance(usage.get(key), int) and usage.get(key) >= 0 else None
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    if (normalized_usage["total_tokens"] is None
            and normalized_usage["input_tokens"] is not None
            and normalized_usage["output_tokens"] is not None):
        normalized_usage["total_tokens"] = (
            normalized_usage["input_tokens"] + normalized_usage["output_tokens"]
        )

    return {
        "status": status,
        "summary": summary.strip(),
        "usage": normalized_usage,
        "response_id": response.get("response_id"),
    }

def link_valid_pmids(text: str, pmids: Iterable[str]) -> str:
    """Link plain PMID citations when their IDs are in the supplied PMID list."""

    valid_pmids = {str(pmid).strip() for pmid in pmids}
    citation_pattern = re.compile(
        r"\[((?:PMID:\s*\d+)(?:\s*,\s*PMID:\s*\d+)*)\](?!\s*\()",
        re.IGNORECASE,
    )

    def replace_group(match: re.Match) -> str:
        citations = re.findall(r"PMID:\s*(\d+)", match.group(1), re.IGNORECASE)
        linked_citations = []

        for pmid in citations:
            citation = f"[PMID: {pmid}]"
            if pmid in valid_pmids:
                citation = (
                    f"[PMID: {pmid}]"
                    f"(https://pubmed.ncbi.nlm.nih.gov/{pmid}/)"
                )
            linked_citations.append(citation)

        return ", ".join(linked_citations)

    return citation_pattern.sub(replace_group, text)

def process_metabolite(
    metabolite: str,
    task: str,
    keyword: str,
    usage_collector: list[dict] | None = None,
) -> dict:
    """
    Analyze one metabolite and return a structured result record.
    """
    try:
        combined_abstract, pmids = fetch_abstracts(metabolite, keyword)
        if not combined_abstract:
            return {
                "metabolite": str(metabolite),
                "summary": "No PubMed abstracts were retrieved for this metabolite and search focus.",
                "status": "no_results",
                "pmids": [],
            }
        analysis = analyze_abstract(combined_abstract, task)
        if usage_collector is not None:
            usage_collector.append({
                "model": model_name,
                "usage": analysis["usage"],
                "response_id": analysis.get("response_id"),
            })
        return {
            "metabolite": str(metabolite),
            "summary": analysis["summary"],
            "status": analysis["status"],
            "pmids": pmids,
        }

    except Exception as e:
        return {
            "metabolite": str(metabolite),
            "summary": f"Processing failed: {str(e)}",
            "status": "error",
            "pmids": [],
        }

def build_markdown_report(results: list[dict]) -> str:
    """Format structured metabolite results as Markdown for display and PDF."""
    report = "# Metabolite Report\n"
    for result in results:
        metabolite = result.get("metabolite", "Unknown metabolite")
        summary = result.get("summary", "")
        pmids = result.get("pmids", [])
        linked_summary = link_valid_pmids(summary, pmids)
        report += f"\n## {metabolite}\n\n{linked_summary}\n"
    return report

def get_csv_bytes(results: list[dict]) -> bytes:
    """Serialize structured metabolite results as UTF-8 CSV bytes."""
    output = io.StringIO(newline="")
    fieldnames = ["Metabolite", "Summary", "Status", "PMIDs"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for result in results:
        summary = result.get("summary", "")
        allowed_pmids = {str(pmid).strip() for pmid in result.get("pmids", [])}
        cited_pmids = list(dict.fromkeys(
            pmid for pmid in re.findall(r"PMID:\s*(\d+)", summary, re.IGNORECASE)
            if pmid in allowed_pmids
        ))
        # Ensure CSV values remain plain text if the model unexpectedly returns Markdown links.
        summary = re.sub(
            r"\[PMID:\s*(\d+)\]\(https://pubmed\.ncbi\.nlm\.nih\.gov/\1/?\)",
            r"[PMID: \1]",
            summary,
            flags=re.IGNORECASE,
        )
        writer.writerow({
            "Metabolite": result.get("metabolite", ""),
            "Summary": summary,
            "Status": result.get("status", "complete"),
            "PMIDs": "; ".join(cited_pmids),
        })

    return output.getvalue().encode("utf-8-sig")

def get_xlsx_bytes(results: list[dict]) -> bytes:
    """Build an Excel workbook with summaries and clickable PMID references."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    workbook = Workbook()
    summaries_sheet = workbook.active
    summaries_sheet.title = "Summaries"
    summaries_sheet.append(["Metabolite", "Summary", "Status"])

    references_sheet = workbook.create_sheet("References")
    references_sheet.append(["Metabolite", "PMID", "PubMed URL"])

    for sheet in (summaries_sheet, references_sheet):
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True)

    for result in results:
        metabolite = str(result.get("metabolite", ""))
        summary = str(result.get("summary", ""))
        status = str(result.get("status", "complete"))
        summaries_sheet.append([metabolite, summary, status])
        summaries_sheet.cell(summaries_sheet.max_row, 2).alignment = Alignment(
            vertical="top", wrap_text=True
        )

        allowed_pmids = {str(pmid).strip() for pmid in result.get("pmids", [])}
        cited_pmids = list(dict.fromkeys(
            pmid for pmid in re.findall(r"PMID:\s*(\d+)", summary, re.IGNORECASE)
            if pmid in allowed_pmids
        ))
        for pmid in cited_pmids:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            references_sheet.append([metabolite, f"PMID: {pmid}", url])
            pmid_cell = references_sheet.cell(references_sheet.max_row, 2)
            pmid_cell.hyperlink = url
            pmid_cell.style = "Hyperlink"

    summaries_sheet.column_dimensions["A"].width = 24
    summaries_sheet.column_dimensions["B"].width = 90
    summaries_sheet.column_dimensions["C"].width = 16
    for row in summaries_sheet.iter_rows(min_row=2):
        summaries_sheet.row_dimensions[row[0].row].height = 45

    references_sheet.column_dimensions["A"].width = 24
    references_sheet.column_dimensions["B"].width = 22
    references_sheet.column_dimensions["C"].width = 42
    summaries_sheet.auto_filter.ref = summaries_sheet.dimensions
    references_sheet.auto_filter.ref = references_sheet.dimensions

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()

def write_report(report: str, file_name: str):
    with open(f"{file_name}.md", "w", encoding="utf-8") as md_file:
        md_file.write(report)
    with open(f"{file_name}.md", "r", encoding="utf-8") as md_file:
        markdown_content = md_file.read()

    pdf = MarkdownPdf()
    pdf.add_section(Section(markdown_content))
    pdf.save(f"{file_name}.pdf")

def get_pdf_bytes(report_text: str):
    """Generates a PDF from markdown and returns the raw bytes."""
    pdf = MarkdownPdf()
    pdf.add_section(Section(report_text))

    # Permission fix: Use a manual path to ensure file is closed
    fd, temp_path = tempfile.mkstemp(suffix=".pdf")
    try:
        os.close(fd)  # Close the file descriptor immediately
        pdf.save(temp_path)  # Let the library write to the closed path

        with open(temp_path, "rb") as f:
            pdf_bytes = f.read()

        return pdf_bytes
    finally:
        # Now it is safe to remove because no process is holding the file
        if os.path.exists(temp_path):
            os.remove(temp_path)

def analyze_metabolite(
    metabolite: str,
    keyword: str,
    usage_collector: list[dict] | None = None,
) -> dict:
    if not NCBI_API_KEY:
        return {
            "metabolite": str(metabolite),
            "summary": "NCBI API key not found. Check your .env file.",
            "status": "error",
            "pmids": [],
        }
    elif not os.environ.get("OPENAI_API_KEY", "").strip():
        return {
            "metabolite": str(metabolite),
            "summary": "OpenAI API key not found. Set OPENAI_API_KEY in your .env file.",
            "status": "error",
            "pmids": [],
        }
    else:
        analysis_task = (
                f"Summarize the biological roles or findings supported by the provided PubMed "
                f"abstracts for the metabolite {metabolite}."
                + (f" Focus on this search topic: {keyword}." if keyword and keyword.strip() else "")
                + " Follow the required format and evidence rules in your system instructions. "
                  "Use only the provided abstracts as evidence."
        )
        return process_metabolite(
            metabolite, analysis_task, keyword, usage_collector=usage_collector
        )


def estimate_model_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> dict:
    """Estimate standard API cost from token usage; return unavailable when unknown."""
    pricing = MODEL_PRICING.get(model)
    if (pricing is None or not isinstance(input_tokens, int) or input_tokens < 0
            or not isinstance(output_tokens, int) or output_tokens < 0):
        return {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_usd": None,
            "rate_effective_date": pricing["effective_date"] if pricing else None,
        }

    estimated_usd = (
        input_tokens * pricing["input_usd_per_million_tokens"]
        + output_tokens * pricing["output_usd_per_million_tokens"]
    ) / 1_000_000
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_usd": estimated_usd,
        "rate_effective_date": pricing["effective_date"],
    }


def aggregate_model_usage(usage_records: list[dict]) -> dict:
    """Aggregate successful model calls without treating unknown counts as zero."""
    models = list(dict.fromkeys(record.get("model") for record in usage_records))
    model = models[0] if len(models) == 1 else (", ".join(models) if models else model_name)
    input_counts = [record.get("usage", {}).get("input_tokens") for record in usage_records]
    output_counts = [record.get("usage", {}).get("output_tokens") for record in usage_records]
    input_tokens = sum(input_counts) if input_counts and all(
        isinstance(count, int) and count >= 0 for count in input_counts
    ) else None
    output_tokens = sum(output_counts) if output_counts and all(
        isinstance(count, int) and count >= 0 for count in output_counts
    ) else None
    total_tokens = input_tokens + output_tokens if (
        input_tokens is not None and output_tokens is not None
    ) else None
    cost = estimate_model_cost(model, input_tokens, output_tokens)
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_usd": cost["estimated_usd"],
        "rate_effective_date": cost["rate_effective_date"],
        "successful_calls": len(usage_records),
    }


def main(metabolite: str, keyword: str):
    return analyze_metabolite(metabolite, keyword)

if __name__ == "__main__":
    pass
