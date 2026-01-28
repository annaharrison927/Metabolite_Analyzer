import streamlit as st
import pandas as pd
import numpy as np
import time
import MockAnalyzer

if 'analysis_result' not in st.session_state:
    st.session_state['analysis_result'] = None

st.set_page_config(page_title="Metabolite AI Research Assistant", layout="wide")
st.title('Metabolite Analyzer')

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
    st.write(dataframe)

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

if st.button('Click to start analysis'):
    if not agreed_to_terms:
        st.write('Please agree to the terms before proceeding.')
    else:
        result = MockAnalyzer.run_analysis(dataframe)
        st.session_state['analysis_result'] = result

if st.session_state['analysis_result'] is not None:
    st.write(st.session_state['analysis_result'])

