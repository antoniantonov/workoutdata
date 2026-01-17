"""Common utility functions for Polar AccessLink workflow.

This module provides shared helper functions used across multiple modules.
"""
from __future__ import annotations

from typing import Dict, Optional
from pathlib import Path
import pandas as pd
from IPython.display import display
from import_tools import expand_table_with_missing_bpm


def get_field(data: Dict[str, object], *keys: str) -> Optional[object]:
    """Extract field from dictionary trying multiple possible key names.
    
    Args:
        data: Dictionary to extract field from (e.g., exercise dict)
        *keys: Key names to try in order
    
    Returns:
        Value if found, None otherwise
    """
    for key in keys:
        if key in data:
            return data[key]
    return None


def process_vo2max_data_for_calories(v02max_data_path: str | Path) -> pd.DataFrame:
    """
    Process VO2max data to calculate calorie burn per HR.
    
    This function performs all the common data processing logic for calculating
    calories per heart rate from VO2max data. The result can then be imported
    into any database (DuckDB, PostgreSQL, etc.).
    
    Processing steps:
    1. Read CSV data (HR and Calories columns)
    2. Expand table to fill missing HR values with interpolation
    3. Slice data up to maximum HR (exclude cool-down phase)
    4. Sort by HR to group consecutive identical values
    5. Collapse consecutive duplicate HR values by averaging calories
    
    Parameters
    ----------
    v02max_data_path : str or Path
        Path to the CSV file containing VO2max/Calorie data.
        Expected columns: HR, Calories
    
    Returns
    -------
    pd.DataFrame
        Processed DataFrame with columns: HR, Calories, Calories_Second
        Ready for database import
    """
    
    print(f"Reading VO2max data from {v02max_data_path}...")
    df = pd.read_csv(v02max_data_path)
    
    # Keep only HR and Calories columns
    df = df[['HR', 'Calories']]
    print(f"Total rows in original DataFrame: {len(df)}")

    # Expand the table
    expanded_df = expand_table_with_missing_bpm(df)
    print(f"Total rows in expanded DataFrame: {len(expanded_df)}")

    # Set display options to show all rows and columns without truncation
    pd.set_option('display.max_rows', None)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.float_format', '{:.6f}'.format)

    # Find the index of the maximum HR value in expanded_df
    max_hr_value = expanded_df['HR'].max()
    max_hr_index = expanded_df[expanded_df['HR'] == max_hr_value].index[0]

    print(f"Maximum HR value in expanded_df: {max_hr_value} at index {max_hr_index}")

    # Create a subset of the expanded DataFrame from index 0 to the index of maximum HR
    hr_rise_expanded_df = expanded_df.loc[:max_hr_index].copy()
    print(f"Total rows in expanded sliced (0:{max_hr_index}) DataFrame : {len(hr_rise_expanded_df)}")

    # Sorting it because we can have the following sequence of HR
    # e.g., 150, 151, 152, 151, 150.
    # Sorting it will put all the same HR values next to each other, so the collapsing algo
    # below will be able to collapse them properly.
    hr_rise_expanded_df = hr_rise_expanded_df.sort_values(by='HR').reset_index(drop=True)

    # Display the subset of expanded DataFrame (data up to max HR)
    print("\nExpanded data from HR rise (index 0 to max HR):")
    display(hr_rise_expanded_df)

    # Create a group identifier for consecutive identical HR values
    hr_rise_expanded_df['group'] = (hr_rise_expanded_df['HR'] != hr_rise_expanded_df['HR'].shift()).cumsum()

    # Group by both group and HR to collapse only consecutive duplicates
    collapsed_df = hr_rise_expanded_df.groupby(['group', 'HR']).agg({
        'Calories': 'mean',
        'Calories_Second': 'mean'
    }).reset_index().drop('group', axis=1)
    
    print(f"Total rows in collapsed DataFrame: {len(collapsed_df)}")

    # Display the collapsed DataFrame
    print("\nFull collapsed DataFrame:")
    display(collapsed_df)
    
    return collapsed_df


__all__ = [
    'get_field',
    'process_vo2max_data_for_calories',
]
