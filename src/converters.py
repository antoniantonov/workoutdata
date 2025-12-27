"""TCX to CSV conversion utilities.

This module provides functionality to convert Garmin TCX files
to Polar-compatible CSV format for workout data analysis.
"""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def convert_tcx_to_csv(
    tcx_path: Path,
    output_csv_path: Path,
    name: str,
    height: float,
    weight: float,
    hr_max: int,
    hr_sit: int,
    vo2max: int,
    override_date_str: Optional[str] = None,
    override_time_str: Optional[str] = None
) -> Path:
    """Convert TCX file to Polar-compatible CSV format.
    
    The output CSV has two parts:
    1. Metadata rows (2 rows): workout summary information
    2. Time-series rows: per-second heart rate data with relative timestamps
    
    Args:
        tcx_path: Path to input TCX file
        output_csv_path: Optional path for output CSV (default: same name as TCX with .csv extension)
        name: Athlete name (default: "Anton Antonov ")
        height: Height in cm (default: 175.0)
        weight: Weight in kg (default: 78.0)
        hr_max: Maximum heart rate (default: 188)
        hr_sit: Sitting heart rate (default: None)
        vo2max: VO2max value (default: 58)
        override_date_str: Optional date string in DD-MM-YYYY format to use instead of TCX date.
                          Use this when the TCX contains UTC time but you want local time.
        override_time_str: Optional time string in HH:MM:SS format to use instead of TCX time.
                          Use this when the TCX contains UTC time but you want local time.
    
    Returns:
        Path to the created CSV file
    
    Raises:
        FileNotFoundError: If TCX file doesn't exist
        ValueError: If TCX parsing fails
    """
    if not tcx_path.exists():
        raise FileNotFoundError(f"TCX file not found: {tcx_path}")
    
    # Parse TCX file
    tree = ET.parse(tcx_path)
    root = tree.getroot()
    
    # Define namespace
    ns = {'tcx': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}
    
    # Extract activity data
    activity = root.find('.//tcx:Activity', ns)
    if activity is None:
        raise ValueError("No Activity found in TCX file")
    
    sport = activity.get('Sport', 'Other').upper()
    
    # Extract lap data
    lap = activity.find('.//tcx:Lap', ns)
    if lap is None:
        raise ValueError("No Lap found in TCX file")
    
    # Extract start time and convert to DD-MM-YYYY format and HH:MM:SS
    start_time_str = lap.get('StartTime')  # e.g., "2025-10-19T00:47:34.000Z"
    start_dt = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
    
    # Use override date/time if provided (for using local time instead of TCX UTC time)
    if override_date_str is not None:
        date_str = override_date_str
    else:
        date_str = start_dt.strftime('%d-%m-%Y')  # DD-MM-YYYY
    
    if override_time_str is not None:
        time_str = override_time_str
    else:
        time_str = start_dt.strftime('%H:%M:%S')  # HH:MM:SS
    
    # Extract metadata
    total_time_seconds = float(lap.find('tcx:TotalTimeSeconds', ns).text)
    duration = str(timedelta(seconds=int(total_time_seconds))).split('.')[0]  # HH:MM:SS format
    
    distance_elem = lap.find('tcx:DistanceMeters', ns)
    distance_km = float(distance_elem.text) / 1000 if distance_elem is not None else 0.0
    
    calories_elem = lap.find('tcx:Calories', ns)
    calories = int(calories_elem.text) if calories_elem is not None else None
    
    avg_hr_elem = lap.find('tcx:AverageHeartRateBpm/tcx:Value', ns)
    avg_hr = int(avg_hr_elem.text) if avg_hr_elem is not None else None
    
    max_hr_elem = lap.find('tcx:MaximumHeartRateBpm/tcx:Value', ns)
    max_hr_workout = int(max_hr_elem.text) if max_hr_elem is not None else None
    
    # Extract notes
    notes_elem = activity.find('tcx:Notes', ns)
    notes = notes_elem.text if notes_elem is not None else ""
    
    # Extract workout name from Training/Plan/Name if available
    plan_name_elem = activity.find('.//tcx:Training/tcx:Plan/tcx:Name', ns)
    if plan_name_elem is not None and plan_name_elem.text:
        sport = plan_name_elem.text.upper()
    
    # Build metadata rows
    metadata_row1_headers = [
        'Name', 'Sport', 'Date', 'Start time', 'Duration', 'Total distance (km)',
        'Average heart rate (bpm)', 'Average speed (km/h)', 'Max speed (km/h)',
        'Average pace (min/km)', 'Max pace (min/km)', 'Calories',
        'Fat percentage of calories(%)', 'Average cadence (rpm)', 'Average stride length (cm)',
        'Running index', 'Training load', 'Ascent (m)', 'Descent (m)',
        'Average power (W)', 'Max power (W)', 'Notes', 'Height (cm)', 'Weight (kg)',
        'HR max', 'HR sit', 'VO2max', ''
    ]
    
    metadata_row2_values = [
        name, sport, date_str, time_str,
        duration, f"{distance_km:.2f}",
        str(avg_hr) if avg_hr else '', '', '', '', '',
        str(calories) if calories else '',
        '', '', '', '', '', '', '', '', '',
        notes,  # CSV writer will properly escape newlines and commas
        str(height), str(weight),
        str(max_hr_workout) if max_hr_workout else str(hr_max), str(hr_sit) if hr_sit else '', str(vo2max) if vo2max else '', ''
    ]
    
    # Extract trackpoints for time-series data
    trackpoints = lap.findall('.//tcx:Trackpoint', ns)
    
    if not trackpoints:
        raise ValueError("No trackpoints found in TCX file")
    
    # Get the first trackpoint timestamp as reference
    first_trackpoint = trackpoints[0]
    first_time_elem = first_trackpoint.find('tcx:Time', ns)
    if first_time_elem is None:
        raise ValueError("First trackpoint missing Time element")
    
    reference_time = datetime.fromisoformat(first_time_elem.text.replace('Z', '+00:00'))
    
    # Build time-series data
    timeseries_headers = [
        'Sample rate', 'Time', 'HR (bpm)', 'Speed (km/h)', 'Pace (min/km)',
        'Cadence', 'Altitude (m)', 'Stride length (m)', 'Distances (m)',
        'Temperatures (C)', 'Power (W)', ''
    ]
    
    timeseries_rows = []
    
    # Process each trackpoint
    for i, tp in enumerate(trackpoints):
        time_elem = tp.find('tcx:Time', ns)
        if time_elem is None:
            continue
        
        tp_time = datetime.fromisoformat(time_elem.text.replace('Z', '+00:00'))
        elapsed = int((tp_time - reference_time).total_seconds()) + 1  # +1 because first data row is at 00:00:01
        
        # Format as HH:MM:SS
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        seconds = elapsed % 60
        time_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        # Extract HR
        hr_elem = tp.find('tcx:HeartRateBpm/tcx:Value', ns)
        hr_value = hr_elem.text if hr_elem is not None else ''
        
        # Build row - first row gets sample rate of 1, rest get empty string
        sample_rate = '1' if i == 0 else ''
        row = [sample_rate, time_formatted, hr_value, '', '', '', '', '', '', '', '', '']
        timeseries_rows.append(row)
    
    # Determine output path
    if output_csv_path is None:
        output_csv_path = tcx_path.with_suffix('.csv')
    
    # Write CSV using csv module for proper escaping
    with open(output_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        
        # Write metadata rows
        writer.writerow(metadata_row1_headers)
        writer.writerow(metadata_row2_values)
        
        # Write timeseries header
        writer.writerow(timeseries_headers)
        
        # Write timeseries data
        for row in timeseries_rows:
            writer.writerow(row)
    
    print(f"✅ Converted TCX to CSV: {output_csv_path}")
    print(f"  - Duration: {duration}")
    print(f"  - Trackpoints: {len(trackpoints)}")
    print(f"  - Average HR: {avg_hr if avg_hr else 'N/A'}")
    
    return output_csv_path


__all__ = [
    'convert_tcx_to_csv',
]
