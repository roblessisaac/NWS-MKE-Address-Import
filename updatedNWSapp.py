import re
import unicodedata
from pathlib import Path

import pandas as pd
import streamlit as st


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

ADDRESS_SHEET = "Address List"
APARTMENT_SHEET = "Apartments"

ADDRESS_COLUMN_ALIASES = {
    "Territory Name": ["Territory Name"],
    "Full House Number": ["FullHouNumber", "Full House Number", "Full_HouseNo", "HouseNo"],
    "Full Street": ["FullStreet", "Full Street", "Street"],
    "Municipality": ["Municipality", "Muni", "Suburb"],
    "State": ["State"],
    "ZIP Code": ["ZipCode", "Zip_Code", "PostalCode"],
    "House Number Main": ["HouseNoMain", "HouseNo", "Number"],
    "House Suffix": ["HouseSx"],
    "Unit Type": ["UnitType"],
    "Unit": ["Unit", "ApartmentNumber"],
    "Latitude": ["Latitude"],
    "Longitude": ["Longitude"],
    "Mailable Address": ["Mailable Address", "FullAddr"],
    "Source Record ID": ["Source record ID", "Source Record ID", "Source_Record_ID"],
    "Data Quality Flag": ["Data Quality Flag"],
}

REQUIRED_CANONICAL_ADDRESS_COLUMNS = [
    "Territory Name",
    "Full House Number",
    "Full Street",
    "Municipality",
    "State",
    "ZIP Code",
    "Latitude",
    "Longitude",
]

REQUIRED_EXPORT_COLUMNS = ["TerritoryID", "CategoryCode", "Category", "Number"]
REQUIRED_APARTMENT_COLUMNS = ["Base Address", "Units", "Territory Name"]


class ImportValidationError(ValueError):
    """Expected user-facing validation failure."""


def clean_scalar(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def clean_text_series(series):
    return (
        series.fillna("")
        .astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def sample_values(series, limit=8):
    values = [clean_scalar(value) for value in series.drop_duplicates().tolist()]
    values = [value for value in values if value]
    return ", ".join(values[:limit])


def normalize_header_names(df):
    df = df.copy()
    df.columns = (
        df.columns.astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    return df


def read_uploaded_table(uploaded_file, sheet_name=None):
    """Read a CSV or Excel upload without relying on the filename alone."""
    filename = getattr(uploaded_file, "name", "").lower()

    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    if filename.endswith(".csv"):
        if sheet_name is not None:
            raise ImportValidationError(
                "The Territory Analysis must be uploaded as the complete Excel workbook "
                "because the Apartments sheet is required."
            )
        return normalize_header_names(pd.read_csv(uploaded_file, low_memory=False))

    if filename.endswith((".xlsx", ".xlsm", ".xls")):
        return normalize_header_names(pd.read_excel(uploaded_file, sheet_name=sheet_name))

    raise ImportValidationError("Only CSV and Excel files are supported.")


def read_analysis_workbook(analysis_file):
    filename = getattr(analysis_file, "name", "").lower()
    if not filename.endswith((".xlsx", ".xlsm", ".xls")):
        raise ImportValidationError(
            "Upload the complete Territory Analysis Excel workbook, not an exported CSV. "
            "The Address List and Apartments sheets are both required."
        )

    if hasattr(analysis_file, "seek"):
        analysis_file.seek(0)

    try:
        workbook = pd.ExcelFile(analysis_file)
    except Exception as exc:
        raise ImportValidationError(f"The Territory Analysis workbook could not be opened: {exc}") from exc

    missing_sheets = [
        sheet for sheet in [ADDRESS_SHEET, APARTMENT_SHEET] if sheet not in workbook.sheet_names
    ]
    if missing_sheets:
        raise ImportValidationError(
            "The Territory Analysis workbook is missing required sheet(s): "
            + ", ".join(missing_sheets)
            + "."
        )

    address_df = normalize_header_names(pd.read_excel(workbook, sheet_name=ADDRESS_SHEET))
    apartments_df = normalize_header_names(pd.read_excel(workbook, sheet_name=APARTMENT_SHEET))
    return address_df, apartments_df


def read_nws_export(export_file):
    filename = getattr(export_file, "name", "").lower()

    if hasattr(export_file, "seek"):
        export_file.seek(0)

    if filename.endswith(".csv"):
        return normalize_header_names(pd.read_csv(export_file, low_memory=False))

    if filename.endswith((".xlsx", ".xlsm", ".xls")):
        try:
            workbook = pd.ExcelFile(export_file)
        except Exception as exc:
            raise ImportValidationError(f"The NWS export workbook could not be opened: {exc}") from exc

        nonempty_candidates = []
        for sheet_name in workbook.sheet_names:
            candidate = normalize_header_names(pd.read_excel(workbook, sheet_name=sheet_name))
            if set(REQUIRED_EXPORT_COLUMNS).issubset(candidate.columns):
                nonempty_candidates.append((sheet_name, candidate))

        if not nonempty_candidates:
            raise ImportValidationError(
                "No sheet in the NWS export contains TerritoryID, CategoryCode, Category, and Number."
            )
        if len(nonempty_candidates) > 1:
            names = ", ".join(name for name, _ in nonempty_candidates)
            raise ImportValidationError(
                "Multiple sheets in the NWS export match the required schema. "
                f"Keep only one export sheet or upload it as CSV. Matching sheets: {names}"
            )
        return nonempty_candidates[0][1]

    raise ImportValidationError("The NWS Territory Export must be a CSV or Excel file.")


def canonicalize_address_columns(df):
    """Map the current Analysis Engine schema and supported legacy aliases."""
    rename_map = {}
    missing = []

    for canonical, aliases in ADDRESS_COLUMN_ALIASES.items():
        match = next((alias for alias in aliases if alias in df.columns), None)
        if match is not None:
            rename_map[match] = canonical
        elif canonical in REQUIRED_CANONICAL_ADDRESS_COLUMNS:
            missing.append(f"{canonical} ({' or '.join(aliases)})")

    if missing:
        raise ImportValidationError(
            "The Address List sheet is missing required field(s): " + ", ".join(missing) + "."
        )

    result = df.rename(columns=rename_map).copy()
    for optional in ADDRESS_COLUMN_ALIASES:
        if optional not in result.columns:
            result[optional] = ""
    return result


def require_columns(df, required, label):
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ImportValidationError(
            f"{label} is missing required column(s): {', '.join(missing)}."
        )


def normalize_zip(series):
    postal = clean_text_series(series).str.replace(r"\s+", "", regex=True)
    malformed = postal.ne("") & ~postal.str.fullmatch(r"\d{5}(?:-\d{4})?")
    if malformed.any():
        raise ImportValidationError(
            "The Address List contains malformed ZIP codes. Examples: "
            + sample_values(postal[malformed])
        )
    return postal


def normalize_address_component(series):
    return (
        clean_text_series(series)
        .str.upper()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def parse_territory_name(value, label="Territory Name"):
    """
    Parse one human-readable territory value into its display name and number.

    The value must end in exactly one integer. Everything before that integer
    is treated as the display territory name. Formatting separators immediately
    before the number are allowed, but malformed values are never repaired.
    """
    original = clean_scalar(value)
    if not original:
        raise ImportValidationError(f"{label} contains a blank territory name.")

    # Capture the final integer without assuming a hyphen-based naming scheme.
    match = re.fullmatch(r"\s*(?P<prefix>.*?)(?P<number>\d+)\s*", original)
    if not match:
        raise ImportValidationError(
            f"{label} must end in an integer territory number. Invalid value: {original}"
        )

    raw_prefix = match.group("prefix").strip()
    number_text = match.group("number")

    # Remove only separators adjacent to the final number. This allows values
    # such as "Hi-Mount 5" and "Hi.Mount-5" without changing the actual name.
    parsed_name = raw_prefix.rstrip(" \t\r\n-–—_.,:;|/\\")
    if not parsed_name:
        raise ImportValidationError(
            f"{label} must contain a display name before the territory number. "
            f"Invalid value: {original}"
        )

    # A digit at the end of the parsed name means the source contained multiple
    # trailing integer tokens, such as "Hi-Mount 5 6".
    if parsed_name[-1].isdigit():
        raise ImportValidationError(
            f"{label} contains multiple trailing integers. Invalid value: {original}"
        )

    territory_number = int(number_text)
    return parsed_name, territory_number


def normalize_display_territory(value):
    """
    Create one Unicode-safe display key component.

    The normalized value is uppercase and contains only Unicode letters and
    digits. Whitespace, hyphens, and all punctuation are removed.
    """
    text = clean_scalar(value)
    normalized = unicodedata.normalize("NFKC", text).upper()
    return "".join(character for character in normalized if character.isalnum())


def build_normalized_display_key(display_name, territory_number):
    """Build the canonical territory key used by both Analysis and NWS data."""
    normalized_name = normalize_display_territory(display_name)
    number_text = clean_scalar(territory_number)

    if not normalized_name:
        raise ImportValidationError("Display territory name is blank after normalization.")
    if not re.fullmatch(r"\d+", number_text):
        raise ImportValidationError(
            f"Territory number must be an integer. Invalid value: {number_text or '<blank>'}"
        )

    # Integer conversion makes leading-zero variants such as 05 and 5 identical.
    return f"{normalized_name}{int(number_text)}"


def parse_territory_series(series, label):
    """Parse and normalize a complete territory-name Series with clear examples."""
    parsed_names = []
    parsed_numbers = []
    normalized_keys = []
    errors = []

    for value in series.tolist():
        try:
            parsed_name, parsed_number = parse_territory_name(value, label)
            parsed_names.append(parsed_name)
            parsed_numbers.append(parsed_number)
            normalized_keys.append(
                build_normalized_display_key(parsed_name, parsed_number)
            )
        except ImportValidationError as error:
            errors.append(str(error))
            parsed_names.append("")
            parsed_numbers.append(pd.NA)
            normalized_keys.append("")

    if errors:
        unique_errors = list(dict.fromkeys(errors))
        raise ImportValidationError(
            f"Some {label} values are malformed. Examples: "
            + " | ".join(unique_errors[:8])
        )

    return (
        pd.Series(parsed_names, index=series.index, dtype="object"),
        pd.Series(parsed_numbers, index=series.index, dtype="Int64"),
        pd.Series(normalized_keys, index=series.index, dtype="object"),
    )


def extract_trailing_territory_number(series, label):
    """
    Backward-compatible wrapper retained for apartment-processing code.

    It uses the same strict territory parser as the display-key pipeline.
    """
    _, parsed_numbers, _ = parse_territory_series(series, label)
    return parsed_numbers


def build_building_key(house_number, street, municipality, postal_code):
    return (
        normalize_address_component(house_number)
        + "|"
        + normalize_address_component(street)
        + "|"
        + normalize_address_component(municipality)
        + "|"
        + normalize_zip(postal_code)
    )


def parse_apartment_base_addresses(apartments_df):
    require_columns(apartments_df, REQUIRED_APARTMENT_COLUMNS, "Apartments sheet")
    apartments = apartments_df.copy()
    apartments = apartments[
        clean_text_series(apartments["Base Address"]).ne("")
    ].copy()

    base_text = clean_text_series(apartments["Base Address"])
    parsed = base_text.str.extract(
        r"^\s*(?P<House>\S+)\s+(?P<Street>.*?),\s*(?P<Municipality>.*?),\s*[A-Za-z]{2}\s+(?P<Zip>\d{5}(?:-\d{4})?)\s*$"
    )
    invalid = parsed.isna().any(axis=1)
    if invalid.any():
        raise ImportValidationError(
            "Some Apartments sheet Base Address values could not be parsed. Examples: "
            + sample_values(base_text[invalid])
        )

    (
        apartments["Parsed Territory Name"],
        apartments["TerritoryNumber"],
        apartments["Normalized Display Territory Key"],
    ) = parse_territory_series(
        apartments["Territory Name"],
        "Apartments sheet Territory Name",
    )
    apartments["ExpectedUnits"] = pd.to_numeric(apartments["Units"], errors="coerce").astype("Int64")
    invalid_units = apartments["ExpectedUnits"].isna() | apartments["ExpectedUnits"].lt(1)
    if invalid_units.any():
        raise ImportValidationError(
            "The Apartments sheet contains blank or invalid unit counts. Examples: "
            + sample_values(apartments.loc[invalid_units, "Base Address"])
        )

    apartments["BuildingKey"] = build_building_key(
        parsed["House"], parsed["Street"], parsed["Municipality"], parsed["Zip"]
    )

    duplicate_keys = apartments.duplicated(
        subset=["Normalized Display Territory Key", "BuildingKey"], keep=False
    )
    if duplicate_keys.any():
        raise ImportValidationError(
            "The Apartments sheet contains duplicate building records. Examples: "
            + sample_values(apartments.loc[duplicate_keys, "Base Address"])
        )

    return apartments[
        [
            "TerritoryNumber",
            "Normalized Display Territory Key",
            "BuildingKey",
            "ExpectedUnits",
            "Base Address",
            "Blank Parent Rows",
            "Nonblank Unit Rows",
            "Duplicate Units",
        ]
    ].copy()


def choose_parent_coordinates(group):
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


def prepare_export(export_df):
    require_columns(export_df, REQUIRED_EXPORT_COLUMNS, "NWS Territory Export")
    export_df = export_df.copy()

    # Remove completely blank trailing Excel rows before validating.
    export_df = export_df.dropna(how="all")
    export_df["TerritoryID"] = clean_text_series(export_df["TerritoryID"])
    export_df["CategoryCode"] = normalize_address_component(export_df["CategoryCode"])
    export_df["Category"] = clean_text_series(export_df["Category"])

    blank_ids = export_df["TerritoryID"].eq("")
    if blank_ids.any():
        raise ImportValidationError(
            f"The NWS export contains {int(blank_ids.sum())} row(s) without a TerritoryID."
        )

    duplicate_ids = export_df["TerritoryID"].duplicated(keep=False)
    if duplicate_ids.any():
        raise ImportValidationError(
            "The NWS export contains duplicate TerritoryID values: "
            + sample_values(export_df.loc[duplicate_ids, "TerritoryID"])
        )

    blank_categories = export_df["Category"].eq("")
    if blank_categories.any():
        raise ImportValidationError(
            f"The NWS export contains {int(blank_categories.sum())} row(s) "
            "without a Category."
        )

    number_text = clean_text_series(export_df["Number"])
    invalid_numbers = ~number_text.str.fullmatch(r"\d+")
    if invalid_numbers.any():
        raise ImportValidationError(
            "The NWS export contains blank or nonnumeric territory numbers. Examples: "
            + sample_values(number_text[invalid_numbers])
        )
    export_df["Number"] = pd.to_numeric(number_text, errors="raise").astype("Int64")

    # Build the NWS-side key from the human-readable Category plus Number.
    export_df["Normalized Display Territory Key"] = [
        build_normalized_display_key(category, number)
        for category, number in zip(export_df["Category"], export_df["Number"])
    ]

    duplicate_keys = export_df["Normalized Display Territory Key"].duplicated(
        keep=False
    )
    if duplicate_keys.any():
        duplicate_rows = (
            export_df.loc[
                duplicate_keys,
                ["Category", "Number", "CategoryCode"],
            ]
            .astype(str)
            .agg(" | ".join, axis=1)
        )
        raise ImportValidationError(
            "The NWS export contains duplicate normalized display territories. "
            "Each Category and Number combination must resolve to exactly one row. "
            "Examples: "
            + sample_values(duplicate_rows)
        )

    return export_df[
        REQUIRED_EXPORT_COLUMNS + ["Normalized Display Territory Key"]
    ].copy()


def create_nws_row(row):
    territory_number = row.get("TerritoryNumber", "")
    territory_number = "" if pd.isna(territory_number) else str(int(territory_number))

    return {
        "TerritoryID": clean_scalar(row.get("TerritoryID", "")),
        "TerritoryNumber": territory_number,
        "CategoryCode": clean_scalar(row.get("CategoryCode", "")),
        "Category": clean_scalar(row.get("Category", "")),
        "TerritoryAddressID": "",
        "ApartmentNumber": "",
        "Number": "",
        "Street": clean_scalar(row.get("Full Street", "")),
        "Suburb": clean_scalar(row.get("Municipality", "")),
        "PostalCode": clean_scalar(row.get("ZIP Code", "")),
        "State": clean_scalar(row.get("State", "WI")) or "WI",
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


def transform_territory_data(analysis_file, export_file):
    address_raw, apartments_raw = read_analysis_workbook(analysis_file)
    export_raw = read_nws_export(export_file)

    addresses = canonicalize_address_columns(address_raw)
    export_df = prepare_export(export_raw)
    apartment_reference = parse_apartment_base_addresses(apartments_raw)

    addresses = addresses.copy()
    addresses["_SourceRow"] = range(2, len(addresses) + 2)

    # Parse each Analysis territory strictly, then build the same canonical key
    # used for the NWS Category + Number combination.
    (
        addresses["Parsed Territory Name"],
        addresses["Parsed Territory Number"],
        addresses["Normalized Display Territory Key"],
    ) = parse_territory_series(
        addresses["Territory Name"],
        "Address List Territory Name",
    )
    addresses["TerritoryNumber"] = addresses["Parsed Territory Number"]

    addresses["Full House Number"] = normalize_address_component(addresses["Full House Number"])
    addresses["Full Street"] = normalize_address_component(addresses["Full Street"])
    addresses["Municipality"] = normalize_address_component(addresses["Municipality"])
    addresses["State"] = normalize_address_component(addresses["State"])
    addresses["ZIP Code"] = normalize_zip(addresses["ZIP Code"])
    addresses["Unit Type"] = normalize_address_component(addresses["Unit Type"])
    addresses["Unit"] = normalize_address_component(addresses["Unit"])

    # Build one logical apartment identifier. Prefer the explicit Unit value,
    # then use Unit Type for identifiers such as FRONT, REAR, UPPER, or LOWER.
    # Generic labels are not treated as apartment numbers.
    generic_unit_types = {"", "APT", "APARTMENT", "UNIT", "SUITE", "STE", "#"}
    addresses["Derived Unit Identifier"] = addresses["Unit"]
    unit_type_fallback = (
        addresses["Derived Unit Identifier"].eq("")
        & ~addresses["Unit Type"].isin(generic_unit_types)
    )
    addresses.loc[
        unit_type_fallback,
        "Derived Unit Identifier",
    ] = addresses.loc[unit_type_fallback, "Unit Type"]

    addresses["Source Record ID"] = clean_text_series(addresses["Source Record ID"])
    addresses["Data Quality Flag"] = clean_text_series(addresses["Data Quality Flag"])

    addresses["Latitude"] = pd.to_numeric(addresses["Latitude"], errors="coerce")
    addresses["Longitude"] = pd.to_numeric(addresses["Longitude"], errors="coerce")

    addresses["BuildingKey"] = build_building_key(
        addresses["Full House Number"],
        addresses["Full Street"],
        addresses["Municipality"],
        addresses["ZIP Code"],
    )

    critical_checks = {
        "territory name": clean_text_series(addresses["Territory Name"]).eq(""),
        "house number": addresses["Full House Number"].eq(""),
        "street": addresses["Full Street"].eq(""),
        "municipality": addresses["Municipality"].eq(""),
        "state": addresses["State"].eq(""),
        "ZIP code": addresses["ZIP Code"].eq(""),
        "valid coordinates": (
            addresses["Latitude"].isna()
            | addresses["Longitude"].isna()
            | ~addresses["Latitude"].between(-90, 90)
            | ~addresses["Longitude"].between(-180, 180)
        ),
    }
    failures = [
        f"{label}: {int(mask.sum())}"
        for label, mask in critical_checks.items()
        if mask.any()
    ]
    if failures:
        raise ImportValidationError(
            "The Address List contains rows that cannot produce a valid NWS import. "
            + "; ".join(failures)
            + "."
        )

    flagged = addresses["Data Quality Flag"].ne("")
    if flagged.any():
        raise ImportValidationError(
            f"The Address List contains {int(flagged.sum())} row(s) with Data Quality Flag values. "
            "Resolve those records in the Analysis Engine before importing. Examples: "
            + sample_values(addresses.loc[flagged, "Data Quality Flag"])
        )

    if addresses["Source Record ID"].ne("").any():
        duplicate_source_ids = (
            addresses["Source Record ID"].ne("")
            & addresses["Source Record ID"].duplicated(keep=False)
        )
        if duplicate_source_ids.any():
            raise ImportValidationError(
                "The Address List contains duplicate Source Record ID values: "
                + sample_values(addresses.loc[duplicate_source_ids, "Source Record ID"])
            )

    # Match exclusively on the human-readable normalized display key.
    # CategoryCode is copied from the matched NWS row only after linkage.
    try:
        addresses = addresses.merge(
            export_df,
            on="Normalized Display Territory Key",
            how="left",
            validate="many_to_one",
            suffixes=("", "_Export"),
        )
    except pd.errors.MergeError as error:
        raise ImportValidationError(
            "One or more Analysis territories matched multiple NWS rows. "
            "The NWS normalized display territory keys must be unique."
        ) from error

    unmatched = addresses["TerritoryID"].isna()
    if unmatched.any():
        raise ImportValidationError(
            f"{int(unmatched.sum())} Address List row(s) could not be matched to the NWS export. "
            "Unmatched territories: "
            + sample_values(addresses.loc[unmatched, "Territory Name"])
        )

    blank_matched_ids = clean_text_series(addresses["TerritoryID"]).eq("")
    if blank_matched_ids.any():
        raise ImportValidationError(
            f"{int(blank_matched_ids.sum())} matched address row(s) have a blank TerritoryID."
        )

    addresses["IsApartmentBuilding"] = addresses.set_index(
        ["Normalized Display Territory Key", "BuildingKey"]
    ).index.isin(
        apartment_reference.set_index(
            ["Normalized Display Territory Key", "BuildingKey"]
        ).index
    )

    # Every building listed on Apartments must exist in Address List.
    present_keys = addresses[
        ["Normalized Display Territory Key", "BuildingKey"]
    ].drop_duplicates()
    apartment_presence = apartment_reference.merge(
        present_keys,
        on=["Normalized Display Territory Key", "BuildingKey"],
        how="left",
        indicator=True,
    )
    missing_buildings = apartment_presence["_merge"].eq("left_only")
    if missing_buildings.any():
        raise ImportValidationError(
            "Some buildings listed on the Apartments sheet were not found in Address List. Examples: "
            + sample_values(apartment_presence.loc[missing_buildings, "Base Address"])
        )

    addresses = addresses.sort_values(
        [
            "TerritoryNumber",
            "Full Street",
            "Full House Number",
            "Unit",
            "_SourceRow",
        ],
        kind="stable",
    )

    final_rows = []
    stats = {
        "input_address_rows": len(addresses),
        "house_rows": 0,
        "apartment_buildings": 0,
        "apartment_parent_rows": 0,
        "apartment_child_rows": 0,
        "source_parent_rows_ignored": 0,
        "excluded_apartment_buildings": 0,
        "excluded_apartment_rows": 0,
    }
    addresses["Exclusion Reason"] = ""

    group_keys = [
        "TerritoryID",
        "TerritoryNumber",
        "Normalized Display Territory Key",
        "BuildingKey",
    ]
    apartment_lookup = apartment_reference.set_index(
        ["Normalized Display Territory Key", "BuildingKey"]
    )

    for (
        _,
        territory_number,
        normalized_display_key,
        building_key,
    ), group in addresses.groupby(
        group_keys, sort=False, dropna=False
    ):
        if not bool(group["IsApartmentBuilding"].iloc[0]):
            for _, row in group.iterrows():
                output = create_nws_row(row)
                output["Type"] = "House"
                output["Number"] = clean_scalar(row["Full House Number"])
                final_rows.append(output)
                stats["house_rows"] += 1
            continue

        reference = apartment_lookup.loc[
            (normalized_display_key, building_key)
        ]
        unit_rows = group[group["Derived Unit Identifier"].ne("")].copy()
        parent_source_rows = group[group["Derived Unit Identifier"].eq("")].copy()

        duplicate_units = unit_rows["Derived Unit Identifier"].duplicated(keep=False)
        expected_units = int(reference["ExpectedUnits"])
        actual_units = int(unit_rows["Derived Unit Identifier"].nunique())

        # A malformed apartment building should not stop unrelated territories.
        # Exclude the entire building from the NWS file and explain the reason
        # in the processing audit instead of creating a partial apartment.
        exclusion_reason = ""
        if duplicate_units.any():
            duplicate_values = sample_values(
                unit_rows.loc[duplicate_units, "Derived Unit Identifier"]
            )
            exclusion_reason = (
                "Duplicate apartment unit identifiers: "
                + duplicate_values
            )
        elif actual_units != expected_units:
            exclusion_reason = (
                f"Apartment unit-count mismatch: Apartments sheet expected "
                f"{expected_units}; resolved {actual_units} unique units"
            )

        if exclusion_reason:
            addresses.loc[group.index, "Exclusion Reason"] = exclusion_reason
            stats["excluded_apartment_buildings"] += 1
            stats["excluded_apartment_rows"] += len(group)
            continue

        first_row = group.iloc[0]
        parent_latitude, parent_longitude = choose_parent_coordinates(group)
        parent = create_nws_row(first_row)
        parent["Type"] = "Apartment"
        parent["Number"] = clean_scalar(first_row["Full House Number"])
        parent["Latitude"] = parent_latitude
        parent["Longitude"] = parent_longitude
        final_rows.append(parent)

        stats["apartment_buildings"] += 1
        stats["apartment_parent_rows"] += 1
        stats["source_parent_rows_ignored"] += len(parent_source_rows)

        for _, row in unit_rows.iterrows():
            child = create_nws_row(row)
            child["Type"] = "Apartment"
            child["Number"] = clean_scalar(first_row["Full House Number"])
            child["ApartmentNumber"] = clean_scalar(
                row["Derived Unit Identifier"]
            )
            child["Latitude"] = parent_latitude
            child["Longitude"] = parent_longitude
            final_rows.append(child)
            stats["apartment_child_rows"] += 1

    output_df = pd.DataFrame(final_rows).reindex(columns=NWS_COLUMNS).fillna("")

    required_output = [
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
    blank_output = {
        column: int(output_df[column].astype(str).str.strip().eq("").sum())
        for column in required_output
    }
    blank_output = {column: count for column, count in blank_output.items() if count}
    if blank_output:
        details = ", ".join(f"{column}: {count}" for column, count in blank_output.items())
        raise ImportValidationError(
            "Final output validation failed because required fields are blank: " + details
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
        raise ImportValidationError(
            f"The final file contains {int(duplicate_children.sum())} duplicate apartment child rows."
        )

    stats["total_export_rows"] = len(output_df)
    audit_columns = [
        "_SourceRow",
        "Source Record ID",
        "Territory Name",
        "Parsed Territory Name",
        "Parsed Territory Number",
        "Normalized Display Territory Key",
        "TerritoryNumber",
        "TerritoryID",
        "CategoryCode",
        "Full House Number",
        "Full Street",
        "Municipality",
        "State",
        "ZIP Code",
        "Unit Type",
        "Unit",
        "Derived Unit Identifier",
        "Exclusion Reason",
        "Latitude",
        "Longitude",
        "BuildingKey",
        "IsApartmentBuilding",
        "Data Quality Flag",
    ]
    audit_df = addresses[audit_columns].copy()
    return output_df, stats, audit_df


# =====================================================================
# Streamlit interface
# =====================================================================
st.set_page_config(
    page_title="TerritoryToolbox's NWS Importer",
    layout="centered",
)

st.title("TerritoryToolbox's NWS Importer")
st.write(
    "Upload the complete Territory Analysis Excel workbook and the matching "
    "NWS Territory Export file. The importer uses the Address List and Apartments "
    "sheets as the source of truth."
)

SESSION_KEYS = [
    "nws_csv_data",
    "nws_audit_csv",
    "nws_stats",
    "nws_processing_success",
]

if "nws_processing_success" not in st.session_state:
    st.session_state.nws_processing_success = False

if not st.session_state.nws_processing_success:
    col1, col2 = st.columns(2)
    with col1:
        uploaded_analysis = st.file_uploader(
            "1. Upload Territory Analysis Workbook",
            type=["xlsx", "xlsm", "xls"],
            key="analysis_upload",
        )
    with col2:
        uploaded_export = st.file_uploader(
            "2. Upload NWS Territory Export",
            type=["xlsx", "xlsm", "xls", "csv"],
            key="export_upload",
        )

    if uploaded_analysis is not None and uploaded_export is not None:
        st.divider()
        if st.button("Generate NWS Import File", type="primary"):
            with st.spinner("Validating territory mappings, addresses, and apartment buildings..."):
                try:
                    final_dataset, stats, audit_df = transform_territory_data(
                        uploaded_analysis, uploaded_export
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
                except ImportValidationError as error:
                    st.error(f"Validation Error: {error}")
                except Exception as error:
                    st.error(
                        "An unexpected processing error occurred. No import file was generated."
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
                "Input Address List rows",
                "House rows generated",
                "Apartment buildings",
                "Apartment parent rows generated",
                "Apartment child rows generated",
                "Source parent rows absorbed",
                "Excluded apartment buildings",
                "Excluded apartment source rows",
                "Total NWS export rows",
            ],
            "Count": [
                stats["input_address_rows"],
                stats["house_rows"],
                stats["apartment_buildings"],
                stats["apartment_parent_rows"],
                stats["apartment_child_rows"],
                stats["source_parent_rows_ignored"],
                stats["excluded_apartment_buildings"],
                stats["excluded_apartment_rows"],
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
            "This optional CSV contains the normalized and matched source rows used "
            "to generate the NWS import."
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
