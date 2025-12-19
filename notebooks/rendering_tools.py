"""Rendering tools for workout data visualization.

This module provides visualization functions for heart rate workout data including:
- Line plots with HR zones overlay
- Pie charts showing time distribution across HR zones
- Support for comparing multiple workouts
"""
from __future__ import annotations

import duckdb
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path
from typing import List, Union


def plot_hr_with_zones(
    workoutIds: Union[str, List[str]],
    db_path: str = "../hr_data/database_v2.duckdb"
) -> None:
    """
    Plots heart rate data with background zones for multiple workouts.

    Parameters:
    - workoutIds: str or list - The ID(s) of the workout(s) to plot.
    - db_path: str - Path to the DuckDB database file.
    """
    
    # Convert single workoutId to list
    if isinstance(workoutIds, str):
        workoutIds = [workoutIds]
    
    # Connect to DuckDB
    con = duckdb.connect(db_path)
    
    # Define color palette for different workouts
    workout_colors = ['blue', 'red', 'green', 'orange', 'purple', 'brown', 'pink', 'gray', 'olive', 'cyan']
    
    # Create figure
    fig = go.Figure()
    
    # Track global min/max for zones
    global_min_hr = float('inf')
    all_first_times = []
    all_last_times = []
    
    # Fetch and plot data for each workout
    for idx, workoutId in enumerate(workoutIds):
        df = con.execute(f"SELECT * FROM timeseries WHERE starts_with(workoutId, '{workoutId}')").fetchdf()
        
        if df.empty:
            print(f"No data found for workoutId: {workoutId}")
            continue
        
        # Update global min HR
        global_min_hr = min(global_min_hr, df['HR (bpm)'].min())
        
        # Track time ranges
        all_first_times.append(df['Time'].iloc[0])
        all_last_times.append(df['Time'].iloc[-1])
        
        # Get max HR for this workout
        max_idx = df['HR (bpm)'].idxmax()
        max_time = df.loc[max_idx, 'Time']
        max_hr = df.loc[max_idx, 'HR (bpm)']
        
        # Add line for this workout
        workout_color = workout_colors[idx % len(workout_colors)]
        fig.add_trace(go.Scatter(
            x=df['Time'],
            y=df['HR (bpm)'],
            mode='lines',
            name=f'Workout {workoutId}',
            line=dict(color=workout_color, width=2),
            hovertemplate='Time: %{x}<br>HR: %{y} bpm<br>Workout: ' + workoutId + '<extra></extra>',
            hoverinfo='none'
        ))
        
        # Add marker for max point
        fig.add_trace(go.Scatter(
            x=[max_time],
            y=[max_hr],
            mode='markers+text',
            marker=dict(color=workout_color, size=10, symbol='circle'),
            text=[f'Max: {max_hr}'],
            textposition='top center',
            showlegend=False,
            hoverinfo='skip'
        ))
    
    con.close()
    
    # If no data was found for any workout
    if global_min_hr == float('inf'):
        print("No data found for any workoutId")
        return
    
    # Get overall time range
    first_time = min(all_first_times)
    last_time = max(all_last_times)
    
    # Load and sort zones CSV
    zones_df = pd.read_csv('../hr_data/zones.csv')
    zones_df = zones_df.sort_values('HR').reset_index(drop=True)
    
    # Build background shapes for zones
    shapes = []
    previous_hr = global_min_hr - 5
    
    colors = ['lightgray', 'green', 'lightblue', 'yellow', 'lightcoral', 'red', 'purple']
    colors = colors[:len(zones_df)]
    
    for i, row in zones_df.iterrows():
        y0 = previous_hr
        y1 = row['HR']
        color = colors[i]
        
        shapes.append(dict(
            type="rect",
            xref="paper", yref="y",
            x0=0, x1=1,
            y0=y0, y1=y1,
            fillcolor=color,
            opacity=0.3,
            layer="below",
            line_width=0,
        ))
        
        previous_hr = y1
    
    # Add zone legend markers
    zone_colors = {i + 1: color for i, color in enumerate(colors)}
    
    for zone, color in zone_colors.items():
        # Get the max HR for this zone from zones_df
        if zone <= len(zones_df):
            max_hr_for_zone = int(zones_df.iloc[zone - 1]['HR'])
            # Special case for the last zone
            if zone == len(zones_df):
                zone_label = f'Zone {zone} (Max HR: {max_hr_for_zone}+)'
            else:
                zone_label = f'Zone {zone} (Max HR: {max_hr_for_zone})'
        else:
            zone_label = f'Zone {zone}'
        
        fig.add_trace(go.Scatter(
            x=[None], y=[None],
            mode='markers',
            marker=dict(size=10),
            name=zone_label,
            legendgroup=f'Zone {zone}',
            showlegend=True,
            marker_color=color
        ))
    
    # Update layout
    fig.update_layout(
        title='Heart Rate Comparison',
        xaxis_title='Time',
        yaxis_title='HR (bpm)',
        shapes=shapes,
        hovermode='x',
        xaxis=dict(
            showspikes=True,
            spikemode='across',
            spikesnap='cursor',
            showline=True,
            spikethickness=1,
            spikecolor="gray",
            spikedash="solid",
            tickvals=[first_time, last_time],
            ticktext=[str(first_time), str(last_time)],
        ),
        yaxis=dict(
            range=[80, None],
            dtick=10
        )
    )
    
    fig.show()


def piechart_hr_with_zones(
    workoutId: str,
    db_path: str = "../hr_data/database_v2.duckdb"
) -> None:
    """
    Plots a pie chart of time spent in each heart rate zone.

    Parameters:
    - workoutId: str - The ID of the workout to plot.
    - db_path: str - Path to the DuckDB database file.
    """
    
    def query_builder(workoutId, table_name):
        return f"SELECT * FROM {table_name}" if workoutId == '' else f"SELECT * FROM {table_name} WHERE starts_with(workoutId, '{workoutId}')"
    
    # Connect to DuckDB and fetch data
    con = duckdb.connect(db_path)

    hr_df = con.execute(query_builder(workoutId, 'timeseries')).fetchdf()

    if hr_df.empty:
        print(f"Cannot piechart. No data found for workoutId: {workoutId}")
        return

    meta_df = con.execute(query_builder(workoutId, 'workout_metadata')).fetchdf()
    calories_df = con.execute("SELECT * FROM calories_per_hr").fetchdf()
    con.close()

    # 1.Get the minimum HR value
    min_hr = hr_df['HR (bpm)'].min()

    # 3. Build zone intervals: each zone is between previous HR and current HR
    zone_bounds = []
    previous_hr = min_hr-5

    zones_df = pd.read_csv('../hr_data/zones.csv')
    zones_df = zones_df.sort_values('HR').reset_index(drop=True)

    # Generating the zone boundaries. E.g. Zone 2 = highest in Zone 1 + 1 to highest in Zone 2
    for i, row in zones_df.iterrows():
        zone_bounds.append((previous_hr, row['HR'], row['Zone']))
        previous_hr = row['HR']

    # 3. Classify each HR value into a zone
    def classify_zone(hr_value):
        for lower, upper, zone in zone_bounds:
            if lower <= hr_value < upper:
                return zone
        return zone_bounds[-1][2]  # Assign to last zone if HR >= max

    # Calculate the zones for each HR value and the calories per second for that HR
    # HR is BPM measured each second.
    hr_df['Zone'] = hr_df['HR (bpm)'].apply(classify_zone)
    hr_df['Calories_Second'] = hr_df['HR (bpm)'].apply(
        lambda hr: calories_df.loc[calories_df['HR'] == hr, 'Calories_Second'].values[0] if hr in calories_df['HR'].values else 0)

    # 4. Calculate percentage of time in each zone
    zone_counts = hr_df['Zone'].value_counts().sort_index()
    zone_percentages = (zone_counts / len(hr_df) * 100).round(2)

    # 5. Display result
    # Calculate total calories burned
    total_calories = hr_df['Calories_Second'].sum()
    print(f"Total calories burned: {total_calories:.2f} kcal")
    # Reading the sum of all metadata calories as the SELECT statement might return multiple rows
    print(f"Total calories burned from metadata source: {meta_df['Calories'].sum()} kcal")
    print("Percentage of time in each HR zone:")
    pie_df = zone_percentages.to_frame(name="Percentage (%)")

    # The reset_index moves the index to a column
    colors = ['lightgray', 'darkgreen', 'lightblue', 'yellow', 'lightcoral']
    pie_df = pie_df.reset_index()
    pie_df['Zone'] = pie_df.apply(
        lambda row: f"{int(row['Zone'])}: {row['Percentage (%)']:.1f}%", axis=1
    )
    pie_df['Time'] = zone_counts.reset_index(drop=True).apply(
        lambda seconds: f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"
    )

    # Extract notes from metadata
    notes = meta_df['Notes'].iloc[0] if 'Notes' in meta_df.columns and not meta_df.empty else ""
    notes_text = notes if notes and pd.notna(notes) else "No notes available"
    
    # Replace newline characters with HTML line breaks for proper rendering
    notes_html = notes_text.replace('\n', '<br>').replace('\r\n', '<br>').replace('\r', '<br>')

    # Create a subplot with two columns and two rows: pie chart, zone table, and notes
    fig = make_subplots(
        rows=2, cols=2, 
        column_widths=[0.6, 0.4],
        row_heights=[0.7, 0.3],
        specs=[
            [{"type": "domain"}, {"type": "table"}],
            [{"type": "table", "colspan": 2}, None]
        ],
        subplot_titles=["Time Spent in Each HR Zone", "Zone Details", "Workout Notes"]
    )

    fig.add_trace(
        go.Pie(
            labels=pie_df['Zone'],
            values=pie_df['Percentage (%)'],
            hole=0.4,  # Optional: donut chart
            marker=dict(colors=colors[:len(pie_df)]),
        ),
        row=1, col=1
    )

    # Add the table to the second column
    fig.add_trace(
        go.Table(
            header=dict(values=["Zone", "Time"], align='center', font=dict(size=12, color='white'), fill_color='darkblue'),
            cells=dict(values=[pie_df['Zone'], pie_df['Time']], align='center', font=dict(size=10), fill_color='lightgray')
        ),
        row=1, col=2
    )

    # Add notes table below
    fig.add_trace(
        go.Table(
            header=dict(values=["Notes"], align='left', font=dict(size=12, color='white'), fill_color='darkblue'),
            cells=dict(
                values=[[notes_html]], 
                align='left', 
                font=dict(size=10), 
                fill_color='lightyellow',
                height=30  # Increase cell height to accommodate multiple lines
            )
        ),
        row=2, col=1
    )

    # Adjust layout to fit both the pie chart and table
    fig.update_layout(
        title_text="Time Spent in Each HR Zone",
        title_x=0.5,  # Center the title
        margin=dict(l=50, r=50, t=80, b=50),  # Adjust margins for better fit
        height=700  # Increase height to accommodate notes
    )

    fig.show()


__all__ = [
    'plot_hr_with_zones',
    'piechart_hr_with_zones',
]
