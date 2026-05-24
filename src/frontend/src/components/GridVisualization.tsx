import React, { useEffect, useRef } from 'react';

export interface Node {
  id: number;
  type: 'gen' | 'battery' | 'load' | 'grid';
  status: number;
  output: number;
  charge_level?: number;
  priority?: number;
  targets: number[];
}

interface GridVisualizationProps {
  nodes: Node[];
  width?: number;
  height?: number;
}

const GridVisualization: React.FC<GridVisualizationProps> = ({
  nodes,
  width = 800,
  height = 500,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Clear canvas
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0, 0, width, height);

    // Calculate node positions in a grid layout
    const nodePositions: { [key: number]: { x: number; y: number } } = {};
    const cols = 3;
    const rows = Math.ceil(nodes.length / cols);
    const nodeSpacingX = width / (cols + 1);
    const nodeSpacingY = height / (rows + 1);

    nodes.forEach((node, index) => {
      const col = index % cols;
      const row = Math.floor(index / cols);
      nodePositions[node.id] = {
        x: nodeSpacingX * (col + 1),
        y: nodeSpacingY * (row + 1),
      };
    });

    // Draw connections/flows between nodes
    ctx.strokeStyle = 'rgba(100, 200, 255, 0.6)';
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 5]);

    nodes.forEach((node) => {
      if (node.targets.length === 0) return;

      const fromPos = nodePositions[node.id];
      if (!fromPos) return;

      node.targets.forEach((targetId) => {
        const toPos = nodePositions[targetId];
        if (!toPos) return;

        // Draw arrow
        const headlen = 15;
        const angle = Math.atan2(toPos.y - fromPos.y, toPos.x - fromPos.x);

        // Draw line
        ctx.beginPath();
        ctx.moveTo(fromPos.x, fromPos.y);
        ctx.lineTo(toPos.x, toPos.y);
        ctx.stroke();

        // Draw arrowhead
        ctx.fillStyle = 'rgba(100, 200, 255, 0.8)';
        ctx.beginPath();
        ctx.moveTo(toPos.x, toPos.y);
        ctx.lineTo(
          toPos.x - headlen * Math.cos(angle - Math.PI / 6),
          toPos.y - headlen * Math.sin(angle - Math.PI / 6)
        );
        ctx.lineTo(
          toPos.x - headlen * Math.cos(angle + Math.PI / 6),
          toPos.y - headlen * Math.sin(angle + Math.PI / 6)
        );
        ctx.closePath();
        ctx.fill();
      });
    });

    // Reset line dash
    ctx.setLineDash([]);

    // Draw nodes
    nodes.forEach((node) => {
      const pos = nodePositions[node.id];
      if (!pos) return;

      const radius = 40;
      const isActive = node.status === 1;

      // Draw node circle
      ctx.fillStyle = isActive ? '#667eea' : '#555555';
      ctx.strokeStyle = isActive ? '#764ba2' : '#333333';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      // Draw node icon (as text/emoji)
      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 24px Arial';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      const icons: { [key: string]: string } = {
        gen: '☀️',
        battery: '🔋',
        load: '🏢',
        grid: '⚡',
      };

      ctx.fillText(icons[node.type] || '?', pos.x, pos.y - 8);

      // Draw node label
      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 12px Arial';
      ctx.fillText(`${node.type.toUpperCase()}`, pos.x, pos.y + 18);

      // Draw output info
      ctx.fillStyle = '#fbbf24';
      ctx.font = '11px Arial';
      ctx.fillText(`${node.output}W`, pos.x, pos.y + 32);
    });

    // Draw legend
    const legendX = 10;
    const legendY = 10;
    const legendItemHeight = 25;

    ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
    ctx.fillRect(legendX, legendY, 150, 130);

    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 13px Arial';
    ctx.textAlign = 'left';
    ctx.fillText('Legend:', legendX + 10, legendY + 18);

    const legends = [
      { icon: '☀️', label: 'Generator' },
      { icon: '🔋', label: 'Battery' },
      { icon: '🏢', label: 'Load' },
      { icon: '⚡', label: 'Grid' },
    ];

    legends.forEach((item, idx) => {
      ctx.font = '12px Arial';
      ctx.fillText(`${item.icon} ${item.label}`, legendX + 10, legendY + 40 + idx * 20);
    });
  }, [nodes, width, height]);

  return (
    <canvas
      ref={canvasRef}
      width={width}
      height={height}
      style={{
        border: '2px solid #667eea',
        borderRadius: '8px',
        backgroundColor: '#1a1a2e',
      }}
    />
  );
};

export default GridVisualization;
