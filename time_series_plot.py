import pandas as pd
import os
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from dash import Dash, dcc, html, Input, Output

# Set the directory for your CSV files and the dates file
DATA_DIR = './TS_0.3_descending'
DATES_FILE = './TS_0.3_descending/dates.txt'

# Read the dates from the file
with open(DATES_FILE, 'r') as f:
    dates = [line.strip() for line in f.readlines()]

# List all CSV files and load data individually
csv_files = sorted(
    [f for f in os.listdir(DATA_DIR) if f.endswith('.csv') and f[:-4].isdigit()],
    key=lambda x: int(x[:-4])  # Exclude the .csv part for sorting
)

# Prepare data for scatter plot
scatter_data = {
    'longitude': [],
    'latitude': [],
    'value': [],
    'date': []
}

# Load each CSV file and extract data
for i, file in enumerate(csv_files):
    input_df = pd.read_csv(os.path.join(DATA_DIR, file), usecols=['Lon', 'Lat', 'u'])
    scatter_data['longitude'].extend(input_df['Lon'])
    scatter_data['latitude'].extend(input_df['Lat'])
    # Scale the value as specified
    scaled_values = input_df['u'] * 56 / (-4) / np.pi
    scatter_data['value'].extend(scaled_values)
    scatter_data['date'].extend([dates[i]] * len(input_df))  # Add corresponding date

# Initialize the Dash app
app = Dash(__name__)

app.layout = html.Div([
    dcc.Graph(
        id='scatter-plot',
        figure=px.scatter(
            scatter_data,
            x='longitude',
            y='latitude',
            color='value',
            hover_name='date',
            title='Scatter Plot of Points',
            color_continuous_scale=px.colors.sequential.Viridis
        ).update_traces(marker=dict(size=5))
    ),
    dcc.Graph(id='time-series-plot'),
    dcc.Store(id='click-data')
])

@app.callback(
    Output('click-data', 'data'),
    Input('scatter-plot', 'clickData')
)
def update_click_data(clickData):
    if clickData:
        return clickData['points'][0]  # Store the clicked point data
    return None

@app.callback(
    Output('time-series-plot', 'figure'),
    Input('click-data', 'data')
)
def update_time_series(clicked_point):
    if clicked_point:
        lon = clicked_point['x']
        lat = clicked_point['y']

        time_series_values = []
        for i, file in enumerate(csv_files):
            input_df = pd.read_csv(os.path.join(DATA_DIR, file), usecols=['Lon', 'Lat', 'u'])
            # Check if the point is close to the clicked point (adjust tolerance if necessary)
            distance = np.sqrt((input_df['Lon'] - lon) ** 2 + (input_df['Lat'] - lat) ** 2)
            # Get all values at the clicked point without averaging
            time_series_values.append(input_df['u'][distance < 0.001].tolist())

        # Flatten the list and convert to numpy for linear fit
        time_series_values_flat = [item for sublist in time_series_values for item in sublist]
        time_series_values_flat_scaled = [value * 56 / (-4) / np.pi for value in time_series_values_flat]
        
        # Create a time series plot
        time_series_fig = go.Figure()
        time_series_fig.add_trace(go.Scatter(x=dates[:len(time_series_values)], y=time_series_values_flat_scaled, mode='lines+markers'))

        # Calculate linear regression for trend line
        if time_series_values_flat_scaled:
            x_values = np.arange(len(time_series_values_flat_scaled))
            coefficients = np.polyfit(x_values, time_series_values_flat_scaled, 1)
            trend_line = np.polyval(coefficients, x_values)

            # Add trend line to the plot
            time_series_fig.add_trace(go.Scatter(x=dates[:len(trend_line)], y=trend_line, mode='lines', name='Trend Line'))

            # Get the slope and intercept for the formula display
            slope, intercept = coefficients
            trend_formula = f'y = {slope:.2f}x + {intercept:.2f}'
            time_series_fig.add_annotation(
                x=0.5,
                y=0.95,
                text=trend_formula,
                showarrow=False,
                font=dict(size=14),
                xref='paper',
                yref='paper'
            )

        time_series_fig.update_layout(title='Time Series for Clicked Point', xaxis_title='Date', yaxis_title='Value (mm)')

        return time_series_fig

    return go.Figure()  # Return an empty figure if no point clicked

if __name__ == '__main__':
    app.run_server(debug=True)
