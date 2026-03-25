import pandas as pd
import mplfinance as mpf


# Specify the date format
date_format = "%d-%b-%Y"


csv = "'/Users/sachdevs/Backend/Projects/Upstox Trigger/Historical Data/Nifty/Nifty_fice_year.csv'"
# csv = 
# Read the CSV file with the specified date format
daily = pd.read_csv(csv, index_col=0, parse_dates=True, date_format=date_format)

daily.index.name = "Date"

daily.shape
daily.head(3)
daily.tail(3)

mpf.plot(daily)
