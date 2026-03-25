class intrument_intra_data:

    def __init__(self,high,low) -> None:
        self.high = high
        self.low  = low
        # self.open = 0
        # self.close= 0
        self.time = 0


    def update_high(self,high):
        self.high = max(self.high,high)

    def update_low(self,low):
        self.low = min(self.low,low)

    def set_low(self,low):
        self.low = low

    def set_time(self,time):
        self.time = time

    def get_high(self):
        return self.high

    def get_low(self):
        return self.low

    def get_time(self):
        return self.time


    def percentage_check_bwn_high_and_low(self):
        gap =  round(((self.high-self.low)/self.low)*100,2)
        return gap