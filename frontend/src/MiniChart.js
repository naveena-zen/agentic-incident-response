import { useEffect, useRef } from 'react';
import {
  Chart, LineController, LineElement, PointElement,
  LinearScale, Filler, Tooltip,
} from 'chart.js';

Chart.register(LineController, LineElement, PointElement, LinearScale, Filler, Tooltip);

/**
 * MiniChart — sparkline using a simple numeric x-axis (no time adapter needed).
 *
 * Props:
 *   data  : array of { timestamp: ISO string, value: number }
 *   color : CSS color
 *   label : series label
 */
export function MiniChart({ data = [], color = '#22c55e', label = '' }) {
  const ref   = useRef(null);
  const chart = useRef(null);

  const buildPoints = (d) =>
    d.map((p, i) => ({ x: i, y: p.value ?? 0 }));

  useEffect(() => {
    if (!ref.current) return;
    const ctx = ref.current.getContext('2d');

    const grad = ctx.createLinearGradient(0, 0, 0, 80);
    grad.addColorStop(0, color + '55');
    grad.addColorStop(1, color + '00');

    chart.current = new Chart(ctx, {
      type: 'line',
      data: {
        datasets: [{
          data: buildPoints(data),
          fill: true,
          backgroundColor: grad,
          borderColor: color,
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.4,
          label,
        }],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
        },
        scales: {
          x: { type: 'linear', display: false },
          y: { display: false, beginAtZero: false },
        },
      },
    });

    return () => { chart.current?.destroy(); chart.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update data without re-mounting
  useEffect(() => {
    if (!chart.current) return;
    const grad = ref.current.getContext('2d').createLinearGradient(0, 0, 0, 80);
    grad.addColorStop(0, color + '55');
    grad.addColorStop(1, color + '00');
    chart.current.data.datasets[0].data             = buildPoints(data);
    chart.current.data.datasets[0].borderColor      = color;
    chart.current.data.datasets[0].backgroundColor  = grad;
    chart.current.update('none');
  }, [data, color]);

  return <canvas ref={ref} style={{ width: '100%', height: '100%' }} />;
}
