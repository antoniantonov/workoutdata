"""Generic utility functions for workout data processing.

This module provides database-agnostic helpers for:
- Interpolating missing heart rate values
- Expanding and interpolating calorie data based on HR
- Deleting files from directories

These functions are used by both DuckDB and PostgreSQL import modules.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import pandas as pd  # type: ignore

from config import load_configuration


def fix_missing_hr(df):
    """
    Fix missing HR values by linear interpolation between known values.
    
    For sequential null HR values, interpolates linearly between the last known
    HR value before the nulls and the first known HR value after the nulls.
    
    :param df: DataFrame with 'HR (bpm)' and 'time' columns
    :return: DataFrame with interpolated HR values
    """
    
    hr_key = 'HR (bpm)'

    # Remove leading rows where HR is None
    first_valid_idx = df[hr_key].first_valid_index()
    # Remove trailing rows where HR is None
    last_valid_idx = df[hr_key].last_valid_index()

    if first_valid_idx is None or last_valid_idx is None:
        print("No valid HR values found.")
        raise ValueError("DataFrame contains no valid HR values.")

    if first_valid_idx > 0 or last_valid_idx < df.shape[0] - 1:
        print(f"Leading trim: [0:{first_valid_idx}], Trailing trim: [{last_valid_idx}:{df.shape[0] - 1}]")
        
    # Keep only rows between first and last valid HR (inclusive)
    df_fixed = df.loc[first_valid_idx:last_valid_idx].copy()
    
    # Find all null positions
    null_mask = df_fixed[hr_key].isnull()
    
    if not null_mask.any():
        print("No missing HR values found.")
        return df_fixed
    
    # Get groups of consecutive nulls
    null_groups = []
    in_null_group = False
    start_idx = None
    
    for i, is_null in enumerate(null_mask):
        if is_null and not in_null_group:
            # Start of a new null group
            start_idx = i
            in_null_group = True
        elif not is_null and in_null_group:
            # End of current null group
            null_groups.append((start_idx, i - 1))
            in_null_group = False
    
    # Handle case where nulls go to the end
    if in_null_group:
        null_groups.append((start_idx, len(df_fixed) - 1))
    
    print(f"Found {len(null_groups)} groups of consecutive null HR values")
    
    # Process each group of nulls
    for group_start, group_end in null_groups:
        null_count = group_end - group_start + 1
        
        # Find the last known HR value before nulls
        before_hr = None
        if group_start > 0:
            before_hr = df_fixed.iloc[group_start - 1][hr_key]

        # Find the first known HR value after nulls
        after_hr = None
        if group_end < len(df_fixed) - 1:
            after_hr = df_fixed.iloc[group_end + 1][hr_key]
        
        print(f"Null group: indices {group_start}-{group_end} ({null_count} nulls)")
        print(f"  Before HR: {before_hr}, After HR: {after_hr}")
        
        # Interpolate values
        if before_hr is not None and after_hr is not None:
            # Linear interpolation between two known values
            # Important: hr_diff is signed value and needs to remain this way in order to calculate delta_per_step correctly
            # if left HR is higher than right HR, we need negative delta_per_step in order to decrement the values.
            hr_diff = after_hr - before_hr  
            delta_per_step = hr_diff / (null_count + 1)
            
            print(f"  HR difference: {hr_diff}, Delta per step: {delta_per_step:.2f}")
            
            for i, null_idx in enumerate(range(group_start, group_end + 1)):
                interpolated_value = round(before_hr + (i + 1) * delta_per_step, 0)
                df_fixed.iloc[null_idx, df_fixed.columns.get_loc(hr_key)] = interpolated_value

        elif before_hr is not None:
            # Only have before value - forward fill
            print(f"  Forward filling with HR: {before_hr}")
            for null_idx in range(group_start, group_end + 1):
                df_fixed.iloc[null_idx, df_fixed.columns.get_loc(hr_key)] = before_hr

        elif after_hr is not None:
            # Only have after value - backward fill
            print(f"  Backward filling with HR: {after_hr}")
            for null_idx in range(group_start, group_end + 1):
                df_fixed.iloc[null_idx, df_fixed.columns.get_loc(hr_key)] = after_hr
        else:
            print(f"  Warning: No surrounding HR values found for interpolation")
    
    # Summary
    remaining_nulls = df_fixed[hr_key].isnull().sum()
    fixed_nulls = null_mask.sum() - remaining_nulls
    
    print(f"\nInterpolation complete:")
    print(f"  Original null values: {null_mask.sum()}")
    print(f"  Fixed values: {fixed_nulls}")
    print(f"  Remaining nulls: {remaining_nulls}")
    
    return df_fixed


def expand_table_with_missing_bpm(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expands the DataFrame to include rows for missing heart rate (HR) values by interpolating
    between existing data points. It also calculates calories per second.

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame containing 'HR' and 'Calories' columns.

    Returns
    -------
    pd.DataFrame
        Expanded DataFrame with interpolated rows and a 'Calories_Second' column.
    """
    # Skip the first two rows (header and units)
    # Assuming the input df includes the units row as the first row of data
    data = df.iloc[1:].copy()
    
    # Convert columns to numeric where applicable
    data['HR'] = pd.to_numeric(data['HR'], errors='coerce')
    data['Calories'] = pd.to_numeric(data['Calories'], errors='coerce')
    
    # Create a list to store expanded rows
    expanded_rows = []
    
    # Iterate through rows to interpolate missing BPM values
    for i in range(len(data) - 1):
        current_row = data.iloc[i]
        next_row = data.iloc[i + 1]
        
        current_bpm = current_row['HR']
        next_bpm = next_row['HR']
        
        # Add the current row to the expanded rows
        expanded_rows.append(current_row)
        
        # Check if there are missing BPM values
        if next_bpm - current_bpm > 1:
            missing_bpm_count = int(next_bpm - current_bpm - 1)
            calorie_diff = (next_row['Calories'] - current_row['Calories']) / (missing_bpm_count + 1)
            
            # Generate missing rows
            for j in range(1, missing_bpm_count + 1):
                interpolated_bpm = current_bpm + j
                interpolated_calories = current_row['Calories'] + calorie_diff * j
                
                # Create a new row with interpolated values
                interpolated_row = current_row.copy()
                interpolated_row['HR'] = interpolated_bpm
                # Calculate calories per second based on the interpolated value
                interpolated_row['Calories'] = interpolated_calories
                
                # Add the interpolated row to the expanded rows
                expanded_rows.append(interpolated_row)
    
    # Add the last row to the expanded rows
    last_row = data.iloc[-1].copy()
    expanded_rows.append(last_row)
    
    # Convert the list of rows back to a DataFrame
    expanded_data = pd.DataFrame(expanded_rows)

    # Add a new column for calories per minute (Wait, code says / 60, so it is per second)
    expanded_data['Calories_Second'] = expanded_data['Calories'] / 60
    
    return expanded_data


def delete_files_from_directory(
    glob_patterns: str | Path | Iterable[str | Path],
    config: dict,
    data_dir: Optional[str | Path] = None
) -> dict[str, int]:
    """
    Delete files from a directory matching the specified glob patterns.

    Parameters
    ----------
    glob_patterns : str, Path, or Iterable[str or Path]
        Glob pattern(s) to match files to delete (e.g., "*.CSV", "*.tcx").
    config : dict
        Configuration dictionary from load_configuration()
    data_dir : str, Path, or None (optional)
        Path to the directory containing files to delete. If None, uses config['OUTPUT_DIR'].

    Returns
    -------
    dict
        Dictionary with deletion statistics:
        {
            "total": int,      # Total number of files matching patterns
            "deleted": int,    # Number of files successfully deleted
            "errors": int,     # Number of files that failed to delete
        }

    Raises
    ------
    ValueError
        If config is None

    Exceptions
    ----------
    Any exceptions during deletion are caught internally; error details are printed,
    and the error count is incremented in the returned dictionary.
    """
    if config is None:
        raise ValueError("config parameter is required and cannot be None")
    
    if data_dir is None:
        data_dir_path = config['OUTPUT_DIR']
    else:
        data_dir_path = Path(data_dir).resolve()
    
    if isinstance(glob_patterns, (str, Path)):
        patterns = [str(glob_patterns)]
    else:
        patterns = [str(pattern) for pattern in glob_patterns]

    file_paths = []
    for pattern in patterns:
        file_paths.extend(sorted(data_dir_path.glob(pattern)))

    # Deduplicate while preserving order
    files = list(dict.fromkeys(file_paths))

    total_files = len(files)
    deleted_files = 0
    error_files = 0

    if not files:
        print(f"No files found matching patterns in {data_dir_path}.")
        return {
            "total": total_files,
            "deleted": deleted_files,
            "errors": error_files,
        }

    print(f"Found {total_files} file(s) matching patterns. Processing...\n")
    
    for file_path in files:
        try:
            # Delete the file
            file_path.unlink()
            print(f"🗑️  Deleted: {file_path.name}")
            deleted_files += 1
            
        except Exception as e:
            error_files += 1
            print(f"❌ Error deleting {file_path.name}: {e}")

    print("\n" + "="*50)
    print("FILE DELETION REPORT")
    print("="*50)
    print(f"Total files found:     {total_files}")
    print(f"Successfully deleted:  {deleted_files}")
    print(f"Errors encountered:    {error_files}")
    print("="*50)

    if total_files > 0:
        deletion_rate = (deleted_files / total_files) * 100
        print(f"Deletion rate:         {deletion_rate:.1f}%")

        if error_files > 0:
            print(f"Warning: {error_files} file(s) encountered errors during deletion.")

    print("="*50)

    return {
        "total": total_files,
        "deleted": deleted_files,
        "errors": error_files,
    }


__all__ = [
    'fix_missing_hr',
    'expand_table_with_missing_bpm',
    'delete_files_from_directory',
]
