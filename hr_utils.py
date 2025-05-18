import pandas as pd

def calculate_hr_zone_percentages(hr_df: pd.DataFrame, zones_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate the percentage of HR samples spent in each HR zone.

    Parameters:
        timeseries_df (pd.DataFrame): DataFrame with columns ['Time', 'HR (bpm)'].
        zones_csv_path (str): Path to CSV file with 'Zone' and 'HR' threshold columns.

    Returns:
        pd.DataFrame: DataFrame with 'Zone' and 'Percentage (%)' columns.
    """

    # Get the minimum HR value
    min_hr = hr_df['HR (bpm)'].min()

    # 3. Build zone intervals: each zone is between previous HR and current HR
    zone_bounds = []
    previous_hr = min_hr-5

    for i, row in zones_df.iterrows():
        zone_bounds.append((previous_hr, row['HR'], row['Zone']))
        previous_hr = row['HR']

    print(zone_bounds)

    # 4. Classify each HR value into a zone
    def classify_zone(hr_value):
        for lower, upper, zone in zone_bounds:
            if lower <= hr_value < upper:
                return zone
        return zone_bounds[-1][2]  # Assign to last zone if HR >= max

    zones_df['Zone'] = hr_df['HR (bpm)'].apply(classify_zone)

    # 5. Calculate percentage of time in each zone
    zone_counts = zones_df['Zone'].value_counts().sort_index()
    zone_percentages = (zone_counts / len(zones_df) * 100).round(2)

    # 6. Display result
    print("Percentage of time in each HR zone:")
    print(zone_percentages.to_frame(name="Percentage (%)"))

    return zone_percentages.to_frame(name="Percentage (%)").reset_index()