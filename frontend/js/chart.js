/**
 * chart.js — LightweightCharts wrapper and management.
 */

export class ChartManager {
    constructor(containerId) {
        this.containerId = containerId;
        this.chart = null;
        this.candleSeries = null;
        this.supertrendSeries = null;
        this.secondarySeries = null;
        this.resizeObserver = null;
        this.lastBar = null;

        this.intervalMap = {
            "1minute": 60,
            "5minute": 300,
            "15minute": 900,
            "30minute": 1800,
            "1hour": 3600,
            "day": 86400
        };

        this.istTimeFormatter = new Intl.DateTimeFormat('en-IN', {
            timeZone: 'Asia/Kolkata',
            hour: 'numeric',
            minute: '2-digit',
            hour12: true,
        });

        this.istDateFormatter = new Intl.DateTimeFormat('en-IN', {
            timeZone: 'Asia/Kolkata',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
        });

        this.istDateTimeFormatter = new Intl.DateTimeFormat('en-IN', {
            timeZone: 'Asia/Kolkata',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: 'numeric',
            minute: '2-digit',
            hour12: true,
        });
    }

    formatIstTimeParts(date) {
        const parts = this.istTimeFormatter.formatToParts(date);
        const hour = parts.find(part => part.type === 'hour')?.value || '';
        const minute = parts.find(part => part.type === 'minute')?.value || '00';
        const dayPeriod = (parts.find(part => part.type === 'dayPeriod')?.value || '').toUpperCase();
        return `${hour}:${minute} ${dayPeriod}`.trim();
    }

    formatIstDateParts(date) {
        const parts = this.istDateFormatter.formatToParts(date);
        const day = parts.find(part => part.type === 'day')?.value || '01';
        const month = parts.find(part => part.type === 'month')?.value || '01';
        const year = parts.find(part => part.type === 'year')?.value || '1970';
        return `${year}-${month}-${day}`;
    }

    toDate(time) {
        if (time && typeof time === 'object' && 'year' in time) {
            return new Date(Date.UTC(time.year, time.month - 1, time.day));
        }
        return new Date(Number(time) * 1000);
    }

    formatCrosshairTime(time) {
        const date = this.toDate(time);
        return `${this.formatIstDateParts(date)}  ${this.formatIstTimeParts(date)} IST`;
    }

    formatTickMark(time, tickMarkType) {
        const date = this.toDate(time);

        if (tickMarkType === LightweightCharts.TickMarkType.Year) {
            return String(date.getUTCFullYear());
        }

        if (
            tickMarkType === LightweightCharts.TickMarkType.Month ||
            tickMarkType === LightweightCharts.TickMarkType.DayOfMonth
        ) {
            return this.formatIstDateParts(date);
        }

        return this.formatIstTimeParts(date);
    }

    init() {
        const container = document.getElementById(this.containerId);
        if (!container) return;

        this.chart = LightweightCharts.createChart(container, {
            layout: {
                background: { type: 'solid', color: '#0a0a0f' },
                textColor: '#8b8b9e',
            },
            grid: {
                vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
                horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
            },
            timeScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
                timeVisible: true,
                secondsVisible: false,
                tickMarkFormatter: (time, tickMarkType) => this.formatTickMark(time, tickMarkType),
            },
            localization: {
                timeFormatter: time => this.formatCrosshairTime(time),
            }
        });

        this.candleSeries = this.chart.addCandlestickSeries({
            upColor: '#00d084',
            downColor: '#ff4757',
            borderVisible: false,
            wickUpColor: '#00d084',
            wickDownColor: '#ff4757',
        });

        this.supertrendSeries = this.chart.addLineSeries({
            lineWidth: 2,
            lineType: LightweightCharts.LineType.Step,
            crosshairMarkerVisible: false,
            color: '#2196F3',
            priceLineVisible: false,
        });

        this.secondarySeries = this.chart.addLineSeries({
            lineWidth: 1.5,
            lineType: LightweightCharts.LineType.Step,
            crosshairMarkerVisible: false,
            color: '#FF9800',
            priceLineVisible: false,
            lineStyle: LightweightCharts.LineStyle.Dashed,
        });

        this.resizeObserver = new ResizeObserver(entries => {
            if (entries.length === 0 || entries[0].target !== container) return;
            const newRect = entries[0].contentRect;
            this.chart.applyOptions({ height: newRect.height, width: newRect.width });
        });
        this.resizeObserver.observe(container);

        container.addEventListener('contextmenu', e => {
            e.preventDefault();
            this.chart.timeScale().fitContent();
            this.chart.priceScale('right').applyOptions({ autoScale: true });
        });
    }

    updateCandle(candle, interval = "1minute") {
        if (!this.candleSeries) return;

        // 1. Round time to interval
        const seconds = this.intervalMap[interval] || 60;
        const roundedTime = Math.floor(Number(candle.time) / seconds) * seconds;

        // 2. Aggregate OHLC if same bar (Compare as Numbers to be safe)
        if (this.lastBar && Number(this.lastBar.time) === Number(roundedTime)) {
            this.lastBar.high = Math.max(this.lastBar.high, candle.high);
            this.lastBar.low = Math.min(this.lastBar.low, candle.low);
            this.lastBar.close = candle.close;
        } else {
            // New bar — open is preserved from the first tick (candle.open = ltp at bar start)
            this.lastBar = { ...candle, time: roundedTime };
        }

        // 3. Pass a copy to ensure the chart re-renders correctly
        this.candleSeries.update({ ...this.lastBar });
    }

    setData(candles) {
        if (this.candleSeries) {
            this.candleSeries.setData(candles);
            // Track the last bar from history for future updates
            if (candles.length > 0) {
                this.lastBar = { ...candles[candles.length - 1] };
            }
        }
    }

    setOverlayData(data, secondaryData) {
        if (this.supertrendSeries) this.supertrendSeries.setData(data || []);
        if (this.secondarySeries) this.secondarySeries.setData(secondaryData || []);
    }

    setMarkers(markers) {
        if (this.candleSeries) this.candleSeries.setMarkers(markers);
    }

    clear() {
        if (this.candleSeries) {
            this.candleSeries.setData([]);
            this.candleSeries.setMarkers([]);
        }
        if (this.supertrendSeries) this.supertrendSeries.setData([]);
        if (this.secondarySeries) this.secondarySeries.setData([]);
        this.lastBar = null;
    }
}
