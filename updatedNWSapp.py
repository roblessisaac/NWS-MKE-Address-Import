import streamlit as st
import pandas as pd


NWS_COLUMNS = [
    "TerritoryID",
    "TerritoryNumber",
    "CategoryCode",
    "Category",
    "TerritoryAddressID",
    "ApartmentNumber",
    "Number",
    "Street",
    "Suburb",
    "PostalCode",
    "State",
    "Name",
    "Phone",
    "Type",
    "Status",
    "NotHomeAttempt",
    "Date1",
    "Date2",
    "Date3",
    "Date4",
    "Date5",
    "Language",
    "Latitude",
    "Longitude",
    "Notes",
    "NotesFromPublisher",
]


def clean_text_series(series):
    """Return trimmed text without pandas NaN or Excel-style .0 endings."""
    return (
        series.fillna("")
        .astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def clean_scalar(value):
    """Convert a scalar value to clean text without exporting NaN."""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def sample_values(series, limit=8):
    values = [clean_scalar(value) for value in series.drop_duplicates().tolist()]
    values = [value for value in values if value]
    return ", ".join(values[:limit])


def require_columns(df, required, file_label):
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"{file_label} is missing required column(s): {', '.join(missing)}."
        )


def extract_city(address):
    """Extract the municipality from 'Street, City, WI ZIP' style text."""
    text = clean_scalar(address)
    if not text:
        return ""

    parts = [part.strip() for part in text.split(",")]
    return parts[1] if len(parts) >= 2 else ""


def normalize_postal_code(series):
    postal = clean_text_series(series)
    postal = postal.str.replace(r"\s+", "", regex=True)

    malformed = postal.ne("") & ~postal.str.fullmatch(r"\d{5}(?:-\d{4})?")
    if malformed.any():
        examples = sample_values(postal[malformed])
        raise ValueError(
            "The Territory Analysis contains malformed postal codes. "
            f"Examples: {examples}"
        )

    return postal


def choose_parent_coordinates(group):
    """
    Choose a deterministic logical coordinate for an apartment building.

    The most common valid coordinate pair is preferred. If every pair occurs
    once, the first pair after deterministic sorting is used.
    """
    coordinates = group[["Latitude", "Longitude"]].dropna()
    if coordinates.empty:
        return "", ""

    counts = (
        coordinates.value_counts()
        .rename("Count")
        .reset_index()
        .sort_values(
            ["Count", "Latitude", "Longitude"],
            ascending=[False, True, True],
            kind="stable",
        )
    )
    best = counts.iloc[0]
    return best["Latitude"], best["Longitude"]


def transform_territory_data(analysis_file, export_file, apartment_threshold=3):
    """
    Transform a TerritoryToolbox Address List CSV and an NWS territory export
    into an NWS address-import DataFrame.

    Returns:
        output_df: Final NWS-formatted data.
        stats: Processing summary.
        audit_df: Source rows with derived matching/classification fields.
    """
    if apartment_threshold < 3:
        raise ValueError("Apartment threshold must be at least 3.")

    analysis_df = pd.read_csv(analysis_file, low_memory=False)
    export_df = pd.read_csv(export_file, low_memory=False)

    analysis_df.columns = (
        analysis_df.columns.astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    export_df.columns = (
        export_df.columns.astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )

    # ------------------------------------------------------------------
    # 1. Schema validation
    # ------------------------------------------------------------------
    required_analysis = [
        "Territory Name",
        "Street",
        "HouseNo",
        "Zip_Code",
        "Latitude",
        "Longitude",
    ]
    required_export = ["CategoryCode", "Number", "TerritoryID", "Category"]

    require_columns(analysis_df, required_analysis, "Territory Analysis CSV")
    require_columns(export_df, required_export, "NWS Territory Export CSV")

    municipality_sources = {"Muni", "Mailable Address", "FullAddr"}
    if not municipality_sources.intersection(analysis_df.columns):
        raise ValueError(
            "The Territory Analysis CSV must contain Muni, Mailable Address, "
            "or FullAddr so the NWS Suburb field can be populated."
        )

    if analysis_df.empty:
        raise ValueError("The Territory Analysis CSV contains no address rows.")
    if export_df.empty:
        raise ValueError("The NWS Territory Export CSV contains no territory rows.")

    analysis_df = analysis_df.copy()
    export_df = export_df.copy()
    analysis_df["_SourceRow"] = range(2, len(analysis_df) + 2)

    # ------------------------------------------------------------------
    # 2. Territory parsing and NWS linkage
    # ------------------------------------------------------------------
    territory_name = clean_text_series(analysis_df["Territory Name"]).str.upper()
    split_cols = territory_name.str.rsplit("-", n=1, expand=True)

    if split_cols.shape[1] != 2:
        raise ValueError(
            "Territory names must end with a hyphen and number, such as IR-1."
        )

    analysis_df["CategoryCode"] = split_cols[0].str.strip()
    territory_number_text = split_cols[1].str.strip()

    invalid_territories = (
        analysis_df["CategoryCode"].eq("")
        | ~territory_number_text.str.fullmatch(r"\d+")
    )
    if invalid_territories.any():
        examples = sample_values(
            analysis_df.loc[invalid_territories, "Territory Name"]
        )
        raise ValueError(
            "Some Territory Name values could not be parsed. "
            f"Examples: {examples}. Expected a format ending in '-number'."
        )

    analysis_df["TerritoryNumber"] = pd.to_numeric(
        territory_number_text, errors="raise"
    ).astype("Int64")

    export_df["CategoryCode"] = clean_text_series(
        export_df["CategoryCode"]
    ).str.upper()
    export_number_text = clean_text_series(export_df["Number"])

    invalid_export_numbers = (
        export_df["CategoryCode"].eq("")
        | ~export_number_text.str.fullmatch(r"\d+")
    )
    if invalid_export_numbers.any():
        examples = sample_values(
            export_df.loc[invalid_export_numbers, "Number"]
        )
        raise ValueError(
            "The NWS export contains blank or nonnumeric territory numbers. "
            f"Examples: {examples}"
        )

    export_df["Number"] = pd.to_numeric(
        export_number_text, errors="raise"
    ).astype("Int64")
    export_df["TerritoryID"] = clean_text_series(export_df["TerritoryID"])
    export_df["Category"] = clean_text_series(export_df["Category"])

    missing_export_ids = export_df["TerritoryID"].eq("")
    if missing_export_ids.any():
        raise ValueError(
            f"The NWS export contains {int(missing_export_ids.sum())} "
            "territory row(s) without a TerritoryID."
        )

    duplicate_export_keys = export_df.duplicated(
        subset=["CategoryCode", "Number"], keep=False
    )
    if duplicate_export_keys.any():
        duplicate_keys = (
            export_df.loc[duplicate_export_keys, ["CategoryCode", "Number"]]
            .drop_duplicates()
            .astype(str)
            .agg("-".join, axis=1)
        )
        raise ValueError(
            "The NWS export contains duplicate CategoryCode/Number keys: "
            f"{sample_values(duplicate_keys)}"
        )

    merged_df = analysis_df.merge(
        export_df[required_export],
        left_on=["CategoryCode", "TerritoryNumber"],
        right_on=["CategoryCode", "Number"],
        how="left",
        validate="many_to_one",
    )

    unmatched = merged_df["TerritoryID"].isna()
    if unmatched.any():
        missing_names = sample_values(
            merged_df.loc[unmatched, "Territory Name"]
        )
        raise ValueError(
            f"{int(unmatched.sum())} address row(s) could not be matched to "
            f"the NWS export. Unmatched territories: {missing_names}"
        )

    # ------------------------------------------------------------------
    # 3. Address normalization and integrity checks
    # ------------------------------------------------------------------
    merged_df["Street"] = (
        clean_text_series(merged_df["Street"])
        .str.replace(r"\s+", " ", regex=True)
        .str.upper()
    )
    merged_df["HouseNo"] = clean_text_series(merged_df["HouseNo"])
    merged_df["HouseSx_Clean"] = (
        clean_text_series(merged_df["HouseSx"])
        if "HouseSx" in merged_df.columns
        else ""
    )
    merged_df["Full_HouseNo"] = (
        merged_df["HouseNo"] + merged_df["HouseSx_Clean"]
    ).str.upper()

    extracted_base = merged_df["Full_HouseNo"].str.extract(r"^(\d+)")[0]
    merged_df["Base_HouseNo"] = extracted_base.fillna(
        merged_df["Full_HouseNo"]
    )

    merged_df["Zip_Code"] = normalize_postal_code(merged_df["Zip_Code"])

    if "Muni" in merged_df.columns:
        suburb = clean_text_series(merged_df["Muni"])
    else:
        suburb = pd.Series("", index=merged_df.index, dtype="object")

    fallback_column = next(
        (
            column
            for column in ["Mailable Address", "FullAddr"]
            if column in merged_df.columns
        ),
        None,
    )
    if fallback_column:
        fallback_suburb = merged_df[fallback_column].apply(extract_city)
        suburb = suburb.mask(suburb.eq(""), fallback_suburb)

    merged_df["Suburb"] = (
        clean_text_series(suburb)
        .str.replace(r"\s+", " ", regex=True)
        .str.upper()
    )
    merged_df["State"] = "WI"

    merged_df["Latitude"] = pd.to_numeric(
        merged_df["Latitude"], errors="coerce"
    )
    merged_df["Longitude"] = pd.to_numeric(
        merged_df["Longitude"], errors="coerce"
    )

    invalid_coordinates = (
        merged_df["Latitude"].isna()
        | merged_df["Longitude"].isna()
        | ~merged_df["Latitude"].between(-90, 90)
        | ~merged_df["Longitude"].between(-180, 180)
    )

    critical_missing = {
        "street": merged_df["Street"].eq(""),
        "house number": merged_df["Full_HouseNo"].eq(""),
        "municipality": merged_df["Suburb"].eq(""),
        "postal code": merged_df["Zip_Code"].eq(""),
        "valid coordinates": invalid_coordinates,
    }
    failures = [
        f"{label}: {int(mask.sum())}"
        for label, mask in critical_missing.items()
        if mask.any()
    ]
    if failures:
        raise ValueError(
            "The Territory Analysis contains address rows that cannot produce "
            "a valid NWS import. " + "; ".join(failures) + "."
        )

    if "Unit" in merged_df.columns:
        merged_df["Explicit_Unit"] = clean_text_series(
            merged_df["Unit"]
        ).str.upper()
    else:
        merged_df["Explicit_Unit"] = ""

    suffix_unit = pd.Series("", index=merged_df.index, dtype="object")
    has_numeric_base = extracted_base.notna()
    suffix_unit.loc[has_numeric_base] = [
        full[len(base):].strip("- /#")
        for full, base in zip(
            merged_df.loc[has_numeric_base, "Full_HouseNo"],
            merged_df.loc[has_numeric_base, "Base_HouseNo"],
        )
    ]
    merged_df["Suffix_Unit"] = suffix_unit
    merged_df["Derived_Unit"] = merged_df["Explicit_Unit"].mask(
        merged_df["Explicit_Unit"].eq(""),
        merged_df["Suffix_Unit"],
    )

    duplicate_identity_columns = [
        "TerritoryID",
        "Street",
        "Suburb",
        "Zip_Code",
        "Full_HouseNo",
        "Derived_Unit",
        "Latitude",
        "Longitude",
    ]
    exact_duplicates = merged_df.duplicated(
        subset=duplicate_identity_columns, keep=False
    )
    if exact_duplicates.any():
        sample_rows = (
            merged_df.loc[
                exact_duplicates,
                ["Territory Name", "Full_HouseNo", "Street", "Derived_Unit"],
            ]
            .astype(str)
            .agg(" | ".join, axis=1)
        )
        raise ValueError(
            f"Found {int(exact_duplicates.sum())} duplicate source rows. "
            f"Examples: {sample_values(sample_rows)}"
        )

    # ------------------------------------------------------------------
    # 4. House/apartment classification
    # ------------------------------------------------------------------
    group_keys = [
        "TerritoryID",
        "Street",
        "Suburb",
        "Zip_Code",
        "Base_HouseNo",
    ]

    merged_df = merged_df.sort_values(
        [
            "CategoryCode",
            "TerritoryNumber",
            "Street",
            "Base_HouseNo",
            "Full_HouseNo",
            "Derived_Unit",
            "_SourceRow",
        ],
        kind="stable",
    )

    final_rows = []
    stats = {
        "input_addresses": len(merged_df),
        "houses": 0,
        "apartment_parents": 0,
        "apartment_children": 0,
        "apartment_buildings": 0,
    }

    def create_nws_row(row):
        territory_number = row.get("TerritoryNumber", "")
        if pd.notna(territory_number):
            territory_number = str(int(territory_number))
        else:
            territory_number = ""

        return {
            "TerritoryID": clean_scalar(row.get("TerritoryID", "")),
            "TerritoryNumber": territory_number,
            "CategoryCode": clean_scalar(row.get("CategoryCode", "")),
            "Category": clean_scalar(row.get("Category", "")),
            "TerritoryAddressID": "",
            "ApartmentNumber": "",
            "Number": "",
            "Street": clean_scalar(row.get("Street", "")),
            "Suburb": clean_scalar(row.get("Suburb", "")),
            "PostalCode": clean_scalar(row.get("Zip_Code", "")),
            "State": "WI",
            "Name": "",
            "Phone": "",
            "Type": "",
            "Status": "Available",
            "NotHomeAttempt": 0,
            "Date1": "",
            "Date2": "",
            "Date3": "",
            "Date4": "",
            "Date5": "",
            "Language": "",
            "Latitude": row.get("Latitude", ""),
            "Longitude": row.get("Longitude", ""),
            "Notes": "",
            "NotesFromPublisher": "",
        }

    for _, group in merged_df.groupby(
        group_keys, sort=False, dropna=False
    ):
        base_number = clean_scalar(group["Base_HouseNo"].iloc[0])

        distinct_explicit_units = group.loc[
            group["Explicit_Unit"].ne(""), "Explicit_Unit"
        ].nunique()
        distinct_derived_units = group.loc[
            group["Derived_Unit"].ne(""), "Derived_Unit"
        ].nunique()

        is_apartment = (
            distinct_explicit_units >= apartment_threshold
            or distinct_derived_units >= apartment_threshold
        )

        if not is_apartment:
            for _, row in group.iterrows():
                output_row = create_nws_row(row)
                output_row["Type"] = "House"
                output_row["Number"] = clean_scalar(row["Full_HouseNo"])
                final_rows.append(output_row)
                stats["houses"] += 1
            continue

        missing_child_units = group["Derived_Unit"].eq("")
        if missing_child_units.any():
            rows = sample_values(group.loc[missing_child_units, "_SourceRow"])
            raise ValueError(
                "An apartment-classified building contains child rows without "
                f"a usable unit number. Source CSV row(s): {rows}"
            )

        first_row = group.iloc[0]
        parent_latitude, parent_longitude = choose_parent_coordinates(group)

        parent_row = create_nws_row(first_row)
        parent_row["Type"] = "Apartment"
        parent_row["Number"] = base_number
        parent_row["Latitude"] = parent_latitude
        parent_row["Longitude"] = parent_longitude
        final_rows.append(parent_row)

        stats["apartment_parents"] += 1
        stats["apartment_buildings"] += 1

        for _, row in group.iterrows():
            child_row = create_nws_row(row)
            child_row["Type"] = "Apartment"
            child_row["Number"] = base_number
            child_row["ApartmentNumber"] = clean_scalar(
                row["Derived_Unit"]
            )
            child_row["Latitude"] = parent_latitude
            child_row["Longitude"] = parent_longitude
            final_rows.append(child_row)
            stats["apartment_children"] += 1

    # ------------------------------------------------------------------
    # 5. Final output validation
    # ------------------------------------------------------------------
    output_df = pd.DataFrame(final_rows).reindex(columns=NWS_COLUMNS)
    output_df = output_df.fillna("")

    required_output_fields = [
        "TerritoryID",
        "TerritoryNumber",
        "CategoryCode",
        "Number",
        "Street",
        "Suburb",
        "PostalCode",
        "State",
        "Type",
        "Status",
        "Latitude",
        "Longitude",
    ]
    missing_output = {
        column: int(output_df[column].astype(str).str.strip().eq("").sum())
        for column in required_output_fields
    }
    missing_output = {
        column: count for column, count in missing_output.items() if count
    }
    if missing_output:
        details = ", ".join(
            f"{column}: {count}"
            for column, count in missing_output.items()
        )
        raise ValueError(
            f"Final output validation failed. Blank required fields: {details}"
        )

    apartment_children = output_df[
        output_df["Type"].eq("Apartment")
        & output_df["ApartmentNumber"].astype(str).str.strip().ne("")
    ]
    duplicate_children = apartment_children.duplicated(
        subset=[
            "TerritoryID",
            "Number",
            "Street",
            "Suburb",
            "PostalCode",
            "ApartmentNumber",
        ],
        keep=False,
    )
    if duplicate_children.any():
        raise ValueError(
            f"The final file contains {int(duplicate_children.sum())} "
            "duplicate apartment child rows."
        )

    stats["total_export_rows"] = len(output_df)
    return output_df, stats, merged_df


# ======================================================================
# Streamlit interface
# ======================================================================
st.set_page_config(
    page_title="TerritoryToolbox's NWS Importer",
    layout="centered",
)

st.title("TerritoryToolbox's NWS Importer")
st.write(
    "Upload the Address List CSV from TerritoryToolbox's Analysis Engine "
    "and the matching NWS Territory Export CSV."
)

SESSION_KEYS = [
    "nws_csv_data",
    "nws_stats",
    "nws_audit_csv",
    "nws_processing_success",
]

if "nws_processing_success" not in st.session_state:
    st.session_state.nws_processing_success = False

if not st.session_state.nws_processing_success:
    with st.expander("Advanced Settings"):
        apartment_threshold = st.selectbox(
            "Apartment grouping threshold",
            options=[3, 4, 5, 6],
            index=0,
            help=(
                "A building is treated as an apartment when it contains at "
                "least this many distinct unit identifiers."
            ),
        )

    col1, col2 = st.columns(2)
    with col1:
        uploaded_analysis = st.file_uploader(
            "1. Upload Address List CSV",
            type=["csv"],
            key="analysis_upload",
        )
    with col2:
        uploaded_export = st.file_uploader(
            "2. Upload NWS Territory Export CSV",
            type=["csv"],
            key="export_upload",
        )

    if uploaded_analysis is not None and uploaded_export is not None:
        st.divider()

        if st.button("Generate NWS Import File", type="primary"):
            with st.spinner(
                "Validating territories, addresses, and apartment groups..."
            ):
                try:
                    final_dataset, stats, audit_df = transform_territory_data(
                        uploaded_analysis,
                        uploaded_export,
                        apartment_threshold=apartment_threshold,
                    )

                    st.session_state.nws_csv_data = final_dataset.to_csv(
                        index=False
                    ).encode("utf-8-sig")
                    st.session_state.nws_audit_csv = audit_df.to_csv(
                        index=False
                    ).encode("utf-8-sig")
                    st.session_state.nws_stats = stats
                    st.session_state.nws_processing_success = True
                    st.rerun()

                except ValueError as error:
                    st.error(f"Validation Error: {error}")
                except Exception as error:
                    st.error(
                        "An unexpected processing error occurred. "
                        "No import file was generated."
                    )
                    with st.expander("Technical Details"):
                        st.exception(error)

else:
    stats = st.session_state.nws_stats

    st.success("NWS import file generated successfully.")

    st.subheader("Process Summary")
    summary = pd.DataFrame(
        {
            "Measure": [
                "Input address rows",
                "House rows generated",
                "Apartment buildings",
                "Apartment parent rows",
                "Apartment child rows",
                "Total export rows",
            ],
            "Count": [
                stats["input_addresses"],
                stats["houses"],
                stats["apartment_buildings"],
                stats["apartment_parents"],
                stats["apartment_children"],
                stats["total_export_rows"],
            ],
        }
    )
    st.dataframe(summary, hide_index=True, use_container_width=True)

    st.download_button(
        label="Download NWS Import CSV",
        data=st.session_state.nws_csv_data,
        file_name="NWS_Address_Import_Ready.csv",
        mime="text/csv",
        type="primary",
    )

    with st.expander("Technical Audit File"):
        st.write(
            "This optional CSV contains the normalized and matched source rows "
            "used to generate the import."
        )
        st.download_button(
            label="Download Processing Audit CSV",
            data=st.session_state.nws_audit_csv,
            file_name="NWS_Address_Import_Audit.csv",
            mime="text/csv",
        )

    if st.button("Process New Files"):
        for key in SESSION_KEYS + ["analysis_upload", "export_upload"]:
            st.session_state.pop(key, None)
        st.rerun()
