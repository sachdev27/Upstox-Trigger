import dash
from dash import dcc
from dash import html
from dash.dependencies import Input, Output
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from intraday_data import worker
from time import sleep

app = dash.Dash(__name__)

# CSS styles to make the graph full-screen
app.css.append_css({
    "external_url": "https://codepen.io/chriddyp/pen/bWLwgP.css"
})

# Layout of the app
app.layout = html.Div([
    dcc.Graph(
        id='live-update-graph',
        style={'height': '90vh', 'width': '95vw'}  # Set the height and width
    ),
    dcc.Interval(
        id='interval-component',
        interval=60*1000,  # in milliseconds
        n_intervals=0
    )
])

# Callback to update the graph
@app.callback(Output('live-update-graph', 'figure'),
              Input('interval-component', 'n_intervals'))
def update_graph_live(n):
    path = f'Historical/Nifty/Intraday-{datetime.today().strftime("%d-%m-%Y")}.csv'
    df = pd.read_csv(path)
    figure = go.Figure(data=[go.Candlestick(x=df['Datetime'],
                                open=df['Open'], high=df['High'],
                                low=df['Low'], close=df['Close'])])
    figure.update_layout(
        title='Live Nifty Chart',
        xaxis_rangeslider_visible=True,
        xaxis_title='Time',
        yaxis_title='Price',
        margin={'l': 0, 'r': 0, 't': 0, 'b': 0},  # Remove margins
    )
    return figure


if __name__ == "__main__":
    nifty = f'Historical/Nifty/Intraday-{datetime.today().strftime("%d-%m-%Y")}.csv'
    banknifty = f'Historical/BankNifty/Intraday-{datetime.today().strftime("%d-%m-%Y")}.csv'
    app.run_server(debug=True)
    worker()
    update_graph_live(nifty)
    sleep(60)