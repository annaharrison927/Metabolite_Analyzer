import streamlit as st
import pandas as pd
import time
import MetaboliteAnalyzer

st.set_page_config(page_title="Metabolite AI Research Assistant", layout="wide")
st.title('Metabolite Analyzer')
if 'analysis_results' not in st.session_state:
    st.session_state['analysis_results'] = None
if 'model_usage' not in st.session_state:
    st.session_state['model_usage'] = None

with st.expander("How to use this tool"):
    left_column, right_column = st.columns([2,1],
                                           vertical_alignment="center",
                                           gap="xxlarge")
    left_column.markdown(
        "Upload a CSV file containing a list of metabolites to generate an AI-powered research summary based "
        "on the most relevant PubMed abstracts. Make sure the title of your first columns says \"Metabolites\". "
        "Enter any keywords that you would like to be included in your search.\n\n")
    right_column.image("example_input.jpg", caption="Input Example")

st.markdown("""
This tool uses NCBI E-utilities. By proceeding, you agree to NCBI's Disclaimer and Copyright notice:
https://www.ncbi.nlm.nih.gov/About/disclaimer.html
""")

agreed_to_terms = False
if st.checkbox('I agree'):
    agreed_to_terms = True

dataframe = None
uploaded_file = st.file_uploader("Choose a file")
if uploaded_file is not None:
    dataframe = pd.read_csv(uploaded_file)

if st.checkbox('Show metabolites'):
    st.subheader('Metabolites')
    if dataframe is None:
        st.write('Please upload a .csv file with a list of metabolites before proceeding.')
    else:
        st.write(dataframe['Metabolites'])


st.text_input("Please enter keyword(s) you would like to add to the search query "
              "(e.g.: \"Maize\" or \"Human health\"):", key="keyword")
keyword = st.session_state.keyword

def show_progress():
    latest_iteration = st.empty()
    bar = st.progress(0)

    for i in range(100):
        # Update the progress bar with each iteration.
        latest_iteration.text(f'Progress: {i + 1}')
        bar.progress(i + 1)
        time.sleep(0.1)

if st.session_state['analysis_results']:
    st.divider()
    try:
        usage_summary = st.session_state.get("model_usage")
        if usage_summary:
            st.subheader("Model usage estimate")
            usage_columns = st.columns(4)
            usage_columns[0].metric("Model", usage_summary["model"])
            usage_columns[1].metric(
                "Input tokens",
                usage_summary["input_tokens"] if usage_summary["input_tokens"] is not None else "Unavailable",
            )
            usage_columns[2].metric(
                "Output tokens",
                usage_summary["output_tokens"] if usage_summary["output_tokens"] is not None else "Unavailable",
            )
            usage_columns[3].metric(
                "Total tokens",
                usage_summary["total_tokens"] if usage_summary["total_tokens"] is not None else "Unavailable",
            )
            if usage_summary["estimated_usd"] is None:
                st.caption("Estimated cost unavailable because token usage or model pricing is unknown.")
            else:
                st.caption(
                    f"Estimated API cost: ${usage_summary['estimated_usd']:.6f} USD "
                    f"using rates effective {usage_summary['rate_effective_date']}. "
                    "This is an estimate and may differ from billed usage."
                )

        output_format = st.selectbox(
            "Output format", ["PDF", "CSV", "Excel workbook"], key="output_format"
        )
        report_markdown = MetaboliteAnalyzer.build_markdown_report(
            st.session_state['analysis_results']
        )
        if output_format == "PDF":
            report_data = MetaboliteAnalyzer.get_pdf_bytes(report_markdown)
            st.download_button(
                label="Download PDF Report",
                data=report_data,
                file_name="metabolite_report.pdf",
                mime="application/pdf"
            )
        elif output_format == "CSV":
            report_data = MetaboliteAnalyzer.get_csv_bytes(
                st.session_state['analysis_results']
            )
            st.download_button(
                label="Download CSV Report",
                data=report_data,
                file_name="metabolite_report.csv",
                mime="text/csv"
            )
        else:
            report_data = MetaboliteAnalyzer.get_xlsx_bytes(
                st.session_state['analysis_results']
            )
            st.download_button(
                label="Download Excel Workbook",
                data=report_data,
                file_name="metabolite_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        if st.checkbox("Show report"):
            if output_format == "PDF":
                st.markdown(report_markdown)
            else:
                st.dataframe(pd.DataFrame(st.session_state['analysis_results']))
    except Exception as e:
        st.error(f"Error generating download: {e}")
    st.divider()

if st.button('Click to start analysis'):
    if not agreed_to_terms:
        st.write('Please agree to the terms before proceeding.')
    elif dataframe is None:
        st.write('Please upload a .csv file with a list of metabolites before proceeding.')
    else:
        results = []
        model_usage_records = []
        metabolites = dataframe['Metabolites'].tolist()
        progress_text = st.empty()
        progress_bar = st.progress(0)
        for i, metabolite in enumerate(metabolites):
            result = MetaboliteAnalyzer.analyze_metabolite(
                metabolite, keyword, usage_collector=model_usage_records
            )
            results.append(result)
            percent_completed = (i+1)/len(metabolites)
            progress_bar.progress(percent_completed)
            progress_text.text(f'{percent_completed * 100}% complete')
        progress_text.text('Done!')
        st.session_state['analysis_results'] = results
        st.session_state['model_usage'] = MetaboliteAnalyzer.aggregate_model_usage(
            model_usage_records
        )
        st.rerun()




