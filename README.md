# Upstox Broker Trading Bot

## Overview

This repository contains a trading bot that interacts with the Upstox broker API to automate trading activities. The bot is designed to execute trades based on predefined strategies, market conditions, and user preferences.

## Features

- **Automated Trading**: Execute trades automatically based on predefined criteria.
- **Strategy Implementation**: Define and implement trading strategies to optimize trading decisions.
- **Real-time Market Data**: Utilize real-time market data to make informed trading decisions.

## Prerequisites

Before running the bot, ensure you have the following:

- Upstox API Key and Secret: Obtain API credentials from the Upstox Developer Console.
- Python 3: The bot is written in Python, so ensure you have Python 3 installed.
- Required Dependencies: Install necessary Python packages by running `pip install -r requirements.txt`.

## Configuration

1. **Clone this repository:**

   ```bash
   git clone https://github.com/sachdev27/upstox-trigger.git


## Usage

- Install dependencies:

```bash
pip install -r requirements.txt
```

- Create .env file with all client variables:

```bash
API_VERSION='2.0'
API_KEY=""
API_SECRET=""
REDIRECT_URI="http://localhost:8210/callback"
AUTH_CODE=""
ACCESS_TOKEN=""
```

### Login

Replace the Client Token(API_KEY) and redirect_uri Accordingly
- For REDIRECT_URI we are going to use a flask app which is present in flask-redirect_uri folder and it sets the AUTH_CODE in it
- Run the Flask app in the folder by (the flask app is set to port 8210)
- Remember, In order th redirect_uri to work - you have to set the same redirect_uri in both the place (Upstox and in the Flask App)

```python
python redirect_uri.py
```

After succesfull login, you will be redirected and the flask app will replace the auth code for you in the .env file

In order to get the Auth Code

Replace the Link with your client_id

(https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=<CLIENT_ID>&redirect_uri=http://localhost:8210/callback/)

- Now You can run the Main or the Websocket_Market File to get the Live Market data

- These file check token first and if it got expired, then replace the token with the new one.

### Run Main Python File:

```bash
python websocket_market.py
python main.py
```


## Disclaimer

This trading bot is for educational and informational purposes only. Trading involves risk, and past performance is not indicative of future results. Use this bot at your own risk.


## Acknowledgments

[Upstox API Documentation](https://upstox.com/developer/api-documentation/open-api)
