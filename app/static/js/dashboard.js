/* Dashboard charts on /. Data comes from a <script type="application/json"> blob
   inlined by the index template, so no fetch is required. */
(function () {
    const dataNode = document.getElementById('chart-data');
    if (!dataNode || typeof Chart === 'undefined') return;

    const D = JSON.parse(dataNode.textContent);
    const PURPLE = '#7c3aed';
    const PURPLE_FILL = 'rgba(124, 58, 237, 0.25)';
    const GRID = 'rgba(255, 255, 255, 0.06)';
    const TICK = '#888';

    Chart.defaults.color = TICK;
    Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

    const baseAxes = {
        x: { grid: { color: GRID }, ticks: { color: TICK } },
        y: { grid: { color: GRID }, ticks: { color: TICK }, beginAtZero: true }
    };

    // Growth: cumulative line
    if (D.growth && D.growth.length) {
        new Chart(document.getElementById('chart-growth'), {
            type: 'line',
            data: {
                labels: D.growth.map(p => p.day),
                datasets: [{
                    data: D.growth.map(p => p.cumulative),
                    borderColor: PURPLE,
                    backgroundColor: PURPLE_FILL,
                    fill: true,
                    tension: 0.25,
                    pointRadius: 0,
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ...baseAxes.x, ticks: { ...baseAxes.x.ticks, maxTicksLimit: 8 } },
                    y: baseAxes.y
                }
            }
        });
    }

    // BPM histogram. Buckets 1..10 = 60..200 BPM in 14-BPM-wide bins; 0 = <60, 11 = >200.
    if (D.bpm && D.bpm.length) {
        const binCounts = new Array(12).fill(0);
        D.bpm.forEach(p => { binCounts[p.bucket] = p.n; });
        const labels = ['<60'];
        for (let i = 0; i < 10; i++) {
            labels.push(`${60 + i * 14}–${60 + (i + 1) * 14}`);
        }
        labels.push('>200');
        new Chart(document.getElementById('chart-bpm'), {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{ data: binCounts, backgroundColor: PURPLE, borderRadius: 4 }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ...baseAxes.x, ticks: { ...baseAxes.x.ticks, maxRotation: 45, minRotation: 45 } },
                    y: baseAxes.y
                }
            }
        });
    }

    // Key distribution: horizontal bars
    if (D.keys && D.keys.length) {
        new Chart(document.getElementById('chart-keys'), {
            type: 'bar',
            data: {
                labels: D.keys.map(p => p.key),
                datasets: [{ data: D.keys.map(p => p.n), backgroundColor: PURPLE, borderRadius: 4 }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ...baseAxes.x, beginAtZero: true },
                    y: baseAxes.y
                }
            }
        });
    }

    // Mood: 10x10 heatmap as a scatter chart with sized points
    if (D.mood && D.mood.length) {
        const max = Math.max(1, ...D.mood.map(p => p.n));
        const points = D.mood.map(p => ({
            x: p.vx,        // valence bucket 1..10
            y: p.ex,        // energy bucket 1..10
            r: 4 + 18 * Math.sqrt(p.n / max),
            n: p.n
        }));
        new Chart(document.getElementById('chart-mood'), {
            type: 'bubble',
            data: {
                datasets: [{
                    data: points,
                    backgroundColor: 'rgba(124, 58, 237, 0.55)',
                    borderColor: '#a78bfa',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => {
                                const p = ctx.raw;
                                return `valence ${p.x}/10, energy ${p.y}/10 — ${p.n} tracks`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ...baseAxes.x,
                        min: 0, max: 11,
                        title: { display: true, text: 'valence (sad → happy)', color: TICK }
                    },
                    y: {
                        ...baseAxes.y,
                        min: 0, max: 11,
                        title: { display: true, text: 'energy (calm → intense)', color: TICK }
                    }
                }
            }
        });
    }
})();
