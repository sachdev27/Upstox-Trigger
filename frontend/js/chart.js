/**
 * chart.js — LightweightCharts wrapper and management.
 */

export class ChartManager {
    constructor(containerId) {
        this.containerId = containerId;
        this.chart = null;
        this.candleSeries = null;
        this.supertrendSeries = null;
        this.resizeObserver = null;
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
            },
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
            color: '#2196F3', // Default Blue, overwritten by data points
            priceLineVisible: false,
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

    updateCandle(candle) {
        if (this.candleSeries) this.candleSeries.update(candle);
    }

    setData(candles) {
        if (this.candleSeries) this.candleSeries.setData(candles);
    }

    setOverlayData(data) {
        if (this.supertrendSeries) this.supertrendSeries.setData(data);
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
    }
}
