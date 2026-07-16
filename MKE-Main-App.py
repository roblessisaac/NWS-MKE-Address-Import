import streamlit as st
import pandas as pd
import numpy as np

def transform_territory_data(analysis_file, export_file):
    """
    Transforms uploaded Territory Analysis and Export CSVs into a valid NWS Address Import DataFrame.
    """
    # Load DataFrames directly from the uploaded files
    analysis_df = pd.read_csv(analysis_file)
    export_df = pd.read_csv(export_file)

    # ---------------------------------------------------------
    # RULE 1: Composite Matching & Linkage
    # ---------------------------------------------------------
    # Split 'Territory Name' (e.g., 'IR-1') into CategoryCode and TerritoryNumber
    analysis_df[['CategoryCode', 'TerritoryNumber']] = analysis_df['Territory Name'].str.split('-', expand=True)
    
    # Ensure numerical matching for the territory number
    analysis_df['TerritoryNumber'] = pd.to_numeric(analysis_df['TerritoryNumber'], errors='coerce')
    export_df['Number'] = pd.to_numeric(export_df['Number'], errors='coerce')

    # Merge BOTH CategoryCode and Number to bring in TerritoryID and Category
    merged_df = pd.merge(
        analysis_df, 
        export_df[['CategoryCode', 'Number', 'TerritoryID', 'Category']], 
        left_on=['CategoryCode', 'TerritoryNumber'], 
        right_on=['CategoryCode', 'Number'], 
        how='left'
    )

    # ---------------------------------------------------------
    # RULE 2: Address Normalization
    # ---------------------------------------------------------
    def extract_city(address):
        if pd.isna(address):
            return ""
        parts = str(address).split(',')
        if len(parts) >= 2:
            return parts[1].strip()
        return ""

    merged_df['Suburb'] = merged_df['Mailable Address'].apply(extract_city)
    merged_df['State'] = 'WI' # Defaulting state to WI
    
    # Extract the purely numeric base from 'HouseNo' (e.g., '1339A' -> '1339')
    merged_df['Base_HouseNo'] = merged_df['HouseNo'].astype(str).str.extract(r'^(\d+)')

    # ---------------------------------------------------------
    # RULE 3: Strict Conditional Apartment vs House Logic
    # ---------------------------------------------------------
    final_rows = []
    
    def create_nws_row(row_data):
        return {
            'TerritoryID': row_data.get('TerritoryID', ''),
            'TerritoryNumber': row_data.get('TerritoryNumber', ''),
            'CategoryCode': row_data.get('CategoryCode', ''),
            'Category': row_data.get('Category', ''),
            'TerritoryAddressID': '',              
            'TerritoryAddressApartmentID': '',     
            'ApartmentNumber': '',
            'Number': '',
            'Street': row_data.get('Street', ''),
            'Suburb': row_data.get('Suburb', ''),
            'PostalCode': row_data.get('Zip_Code', ''),
            'State': row_data.get('State', 'WI'),
            'Name': '',
            'Phone': '',
            'Type': '',
            'Status': 'Available',                 
            'NotHomeAttempt': 0                    
        }

    # Group by Street and Base House Number to evaluate the "Threshold of 3"
    for (street, base_no), group in merged_df.groupby(['Street', 'Base_HouseNo']):
        group_size = len(group)
        
        if group_size < 3:
            # RULE A: Duplexes & Single Homes (Fewer than 3 variations)
            for _, row in group.iterrows():
                nws_row = create_nws_row(row)
                nws_row['Type'] = 'House'
                nws_row['Number'] = row['HouseNo']
                final_rows.append(nws_row)
                
        else:
            # RULE B: Multi-Family Protocol (3 or more variations)
            first_row = group.iloc[0]
            
            # 1. Generate the Parent Row
            parent_row = create_nws_row(first_row)
            parent_row['Type'] = 'Apartment'
            parent_row['Number'] = base_no 
            final_rows.append(parent_row)
            
            # 2. Generate the Child Rows
            for _, row in group.iterrows():
                child_row = create_nws_row(row)
                child_row['Type'] = 'Apartment'
                child_row['Number'] = base_no
                
                if 'Unit' in row and pd.notna(row['Unit']) and str(row['Unit']).strip() != '':
                    child_row['ApartmentNumber'] = str(row['Unit'])
                else:
                    child_row['ApartmentNumber'] = str(row['HouseNo'])
                
                final_rows.append(child_row)

    # ---------------------------------------------------------
    # RULE 4: Final Output Layout
    # ---------------------------------------------------------
    output_df = pd.DataFrame(final_rows)
    
    nws_columns = [
        'TerritoryID', 'TerritoryNumber', 'CategoryCode', 'Category', 
        'TerritoryAddressID', 'TerritoryAddressApartmentID', 'ApartmentNumber', 
        'Number', 'Street', 'Suburb', 'PostalCode', 'State', 
        'Name', 'Phone', 'Type', 'Status', 'NotHomeAttempt'
    ]
    
    output_df = output_df.reindex(columns=nws_columns)
    return output_df

# ==========================================
# Streamlit Web Interface
# ==========================================
st.set_page_config(page_title="NWS Address Importer", page_icon="🗺️")

st.title("🗺️ NWS Address Import Generator")
st.write("Upload your standardized Territory Analysis and the NWS Territory Export to generate a ready-to-import CSV.")

# Create the upload boxes for the user
col1, col2 = st.columns(2)
with col1:
    uploaded_analysis = st.file_uploader("1. Upload Territory Analysis CSV", type="csv")
with col2:
    uploaded_export = st.file_uploader("2. Upload NWS Territory Export CSV", type="csv")

# Only show the generate button if BOTH files are uploaded
if uploaded_analysis and uploaded_export:
    st.divider()
    if st.button("Generate NWS Import File", type="primary"):
        with st.spinner("Processing addresses and formatting for NWS..."):
            try:
                # Pass the uploaded files directly into our engine
                final_dataset = transform_territory_data(uploaded_analysis, uploaded_export)
                
                # Convert the result into a downloadable CSV string
                csv_data = final_dataset.to_csv(index=False).encode('utf-8')
                
                st.success("✅ Transformation Complete! Your file is ready.")
                
                # Provide the download button
                st.download_button(
                    label="⬇️ Download Final NWS Import CSV",
                    data=csv_data,
                    file_name='NWS_Address_Import_Ready.csv',
                    mime='text/csv',
                )
            except Exception as e:
                st.error(f"An error occurred during processing: {e}")