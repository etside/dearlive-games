import React, { useEffect, useRef, useState } from 'react';
import { useGame } from '../contexts/GameContext';
import { useWallet } from '../contexts/WalletContext';

interface GameCanvasProps {
  gameCode: string;
}

export function GameCanvas({ gameCode }: GameCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { currentGame } = useGame();
  const { balance } = useWallet();

  useEffect(() => {
    if (!canvasRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Set up canvas size
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * window.devicePixelRatio;
      canvas.height = rect.height * window.devicePixelRatio;
      ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    };

    resize();
    window.addEventListener('resize', resize);

    // Game rendering loop
    let animationId: number;
    const render = () => {
      if (!ctx) return;
      
      // Clear canvas
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      
      // Draw game background
      drawBackground(ctx, canvas.width, canvas.height);
      
      // Draw game elements based on game type
      // This would connect to the actual game engine
      
      animationId = requestAnimationFrame(render);
    };

    animationId = requestAnimationFrame(render);
    setLoading(false);

    return () => {
      window.removeEventListener('resize', resize);
      cancelAnimationFrame(animationId);
    };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-4 border-purple-500 border-t-transparent"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-400">
        Error loading game: {error}
      </div>
    );
  }

  return (
    <canvas
      ref={canvasRef}
      className="w-full h-full"
      style={{ 
        display: 'block',
        backgroundColor: '#050914',
      }}
    />
  );
}

function drawBackground(ctx: CanvasRenderingContext2D, width: number, height: number) {
  // Draw felt background
  const gradient = ctx.createRadialGradient(
    width / 2, height * 0.4, 60,
    width / 2, height * 0.4, Math.max(width, height) * 0.75
  );
  gradient.addColorStop(0, '#147a52');
  gradient.addColorStop(1, '#083a28');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);
}

export { GameCanvas };
