import pandas as pd
import numpy as np
import re

def transform_territory_data(analysis_csv_path, export_csv_path, output_csv_path):
    """
    Transforms a Territory Analysis CSV into a valid NWS Address Import CSV.
    """
    print("Loading data files...")
    # Load input DataFrames
    analysis_df = pd.read_csv(analysis_csv_path)
    export_df = pd.read_csv(export_csv_path)

    # ---------------------------------------------------------
    # RULE 1: Composite Matching & Linkage
    # ---------------------------------------------------------
    print("Parsing territory names and merging reference keys...")
    
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
    print("Normalizing addresses...")
    
    # Extract Suburb (City) from 'Mailable Address' (Assumes format: "1303 N 57TH ST, Milwaukee, WI 53208")
    def extract_city(address):
        if pd.isna(address):
            return ""
        parts = str(address).split(',')
        if len(parts) >= 2:
            return parts[1].strip()
        return ""

    merged_df['Suburb'] = merged_df['Mailable Address'].apply(extract_city)
    merged_df['State'] = 'WI' # Defaulting state to WI per instructions
    
    # Extract the purely numeric base from 'HouseNo' (e.g., '1339A' -> '1339')
    merged_df['Base_HouseNo'] = merged_df['HouseNo'].astype(str).str.extract(r'^(\d+)')

    # ---------------------------------------------------------
    # RULE 3: Strict Conditional Apartment vs House Logic
    # ---------------------------------------------------------
    print("Applying Apartment/Duplex threshold rules...")
    
    final_rows = []
    
    # Define a helper to construct a clean baseline NWS row
    def create_nws_row(row_data):
        return {
            'TerritoryID': row_data.get('TerritoryID', ''),
            'TerritoryNumber': row_data.get('TerritoryNumber', ''),
            'CategoryCode': row_data.get('CategoryCode', ''),
            'Category': row_data.get('Category', ''),
            'TerritoryAddressID': '',              # System default
            'TerritoryAddressApartmentID': '',     # System default
            'ApartmentNumber': '',
            'Number': '',
            'Street': row_data.get('Street', ''),
            'Suburb': row_data.get('Suburb', ''),
            'PostalCode': row_data.get('Zip_Code', ''),
            'State': row_data.get('State', 'WI'),
            'Name': '',
            'Phone': '',
            'Type': '',
            'Status': 'Available',                 # System default
            'NotHomeAttempt': 0                    # System default
        }

    # Group by Street and Base House Number to evaluate the "Threshold of 3"
    for (street, base_no), group in merged_df.groupby(['Street', 'Base_HouseNo']):
        group_size = len(group)
        
        if group_size < 3:
            # RULE A: Duplexes & Single Homes (Fewer than 3 variations)
            for _, row in group.iterrows():
                nws_row = create_nws_row(row)
                nws_row['Type'] = 'House'
                nws_row['Number'] = row['HouseNo'] # Keep specific format (e.g., '1339a')
                final_rows.append(nws_row)
                
        else:
            # RULE B: Multi-Family Protocol (3 or more variations)
            first_row = group.iloc[0] # Grab the first row to copy base territory data
            
            # 1. Generate the Parent Row
            parent_row = create_nws_row(first_row)
            parent_row['Type'] = 'Apartment'
            parent_row['Number'] = base_no # Base number only (e.g., '1339')
            final_rows.append(parent_row)
            
            # 2. Generate the Child Rows
            for _, row in group.iterrows():
                child_row = create_nws_row(row)
                child_row['Type'] = 'Apartment'
                child_row['Number'] = base_no
                
                # Determine the best apartment designation (Use 'Unit' col if exists, else the raw HouseNo string)
                if 'Unit' in row and pd.notna(row['Unit']) and str(row['Unit']).strip() != '':
                    child_row['ApartmentNumber'] = str(row['Unit'])
                else:
                    child_row['ApartmentNumber'] = str(row['HouseNo'])
                
                final_rows.append(child_row)

    # ---------------------------------------------------------
    # RULE 4: Final Output Layout
    # ---------------------------------------------------------
    print("Formatting final output schema...")
    
    # Convert list of dictionaries back to a DataFrame
    output_df = pd.DataFrame(final_rows)
    
    # Enforce exact column order required by NWS
    nws_columns = [
        'TerritoryID', 'TerritoryNumber', 'CategoryCode', 'Category', 
        'TerritoryAddressID', 'TerritoryAddressApartmentID', 'ApartmentNumber', 
        'Number', 'Street', 'Suburb', 'PostalCode', 'State', 
        'Name', 'Phone', 'Type', 'Status', 'NotHomeAttempt'
    ]
    
    # Reindex to guarantee only these columns exist, in this exact order
    output_df = output_df.reindex(columns=nws_columns)
    
    # Export to CSV
    output_df.to_csv(output_csv_path, index=False)
    print(f"Success! NWS Address Import saved to: {output_csv_path}")

# ==========================================
# Execution Block (How to run the script)
# ==========================================
if __name__ == "__main__":
    # Replace these filenames with your actual local file paths
    ANALYSIS_FILE = 'Territory_Analysis.csv'
    EXPORT_FILE = 'Territory_Export.csv'
    OUTPUT_FILE = 'NWS_Address_Import_Ready.csv'
    
    transform_territory_data(ANALYSIS_FILE, EXPORT_FILE, OUTPUT_FILE)