'use client';

import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import styles from './snake.module.css';

const BOARD_SIZE = 20;
const STARTING_SNAKE = [
  { x: 10, y: 10 },
  { x: 9, y: 10 },
  { x: 8, y: 10 }
];
const STARTING_FOOD = { x: 15, y: 8 };
const INITIAL_DIRECTION = { x: 1, y: 0 };
const FOOD_COLORS = ['#ff5a7b', '#ffb627', '#6ed6a8', '#6c8cff', '#c77dff'];

interface Point {
  x: number;
  y: number;
}

function isSamePoint(left: Point, right: Point): boolean {
  return left.x === right.x && left.y === right.y;
}

function makeFood(snake: Point[]): Point {
  const emptyCells: Point[] = [];
  for (let y = 0; y < BOARD_SIZE; y += 1) {
    for (let x = 0; x < BOARD_SIZE; x += 1) {
      if (!snake.some((segment) => segment.x === x && segment.y === y)) emptyCells.push({ x, y });
    }
  }
  return emptyCells[Math.floor(Math.random() * emptyCells.length)] ?? STARTING_FOOD;
}

export default function SnakeGame() {
  const [snake, setSnake] = useState<Point[]>(STARTING_SNAKE);
  const [food, setFood] = useState<Point>(STARTING_FOOD);
  const [score, setScore] = useState(0);
  const [bestScore, setBestScore] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isGameOver, setIsGameOver] = useState(false);
  const directionRef = useRef(INITIAL_DIRECTION);
  const queuedDirectionRef = useRef(INITIAL_DIRECTION);

  useEffect(() => {
    setBestScore(Number(window.localStorage.getItem('rainbow-snake-best') ?? 0));
  }, []);

  const changeDirection = useCallback((next: Point) => {
    const current = directionRef.current;
    if (next.x === -current.x && next.y === -current.y) return;
    queuedDirectionRef.current = next;
  }, []);

  const restart = useCallback(() => {
    directionRef.current = INITIAL_DIRECTION;
    queuedDirectionRef.current = INITIAL_DIRECTION;
    setSnake(STARTING_SNAKE);
    setFood(STARTING_FOOD);
    setScore(0);
    setIsGameOver(false);
    setIsPlaying(true);
  }, []);

  const move = useCallback(() => {
    setSnake((currentSnake) => {
      const nextDirection = queuedDirectionRef.current;
      directionRef.current = nextDirection;
      const head = currentSnake[0];
      const nextHead = { x: head.x + nextDirection.x, y: head.y + nextDirection.y };
      const ateFood = isSamePoint(nextHead, food);
      const bodyToCheck = ateFood ? currentSnake : currentSnake.slice(0, -1);
      const hitWall = nextHead.x < 0 || nextHead.x >= BOARD_SIZE || nextHead.y < 0 || nextHead.y >= BOARD_SIZE;
      const hitSelf = bodyToCheck.some((segment) => isSamePoint(segment, nextHead));

      if (hitWall || hitSelf) {
        setIsPlaying(false);
        setIsGameOver(true);
        return currentSnake;
      }

      const nextSnake = [nextHead, ...currentSnake];
      if (ateFood) {
        setScore((currentScore) => {
          const nextScore = currentScore + 10;
          setBestScore((currentBest) => {
            const nextBest = Math.max(currentBest, nextScore);
            window.localStorage.setItem('rainbow-snake-best', String(nextBest));
            return nextBest;
          });
          return nextScore;
        });
        setFood(makeFood(nextSnake));
        return nextSnake;
      }
      return nextSnake.slice(0, -1);
    });
  }, [food]);

  useEffect(() => {
    if (!isPlaying) return;
    const timer = window.setInterval(move, 105);
    return () => window.clearInterval(timer);
  }, [isPlaying, move]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const controls: Record<string, Point> = {
        ArrowUp: { x: 0, y: -1 }, w: { x: 0, y: -1 }, W: { x: 0, y: -1 },
        ArrowDown: { x: 0, y: 1 }, s: { x: 0, y: 1 }, S: { x: 0, y: 1 },
        ArrowLeft: { x: -1, y: 0 }, a: { x: -1, y: 0 }, A: { x: -1, y: 0 },
        ArrowRight: { x: 1, y: 0 }, d: { x: 1, y: 0 }, D: { x: 1, y: 0 }
      };
      if (event.key === ' ') {
        event.preventDefault();
        if (!isGameOver) setIsPlaying((playing) => !playing);
        return;
      }
      if (controls[event.key]) {
        event.preventDefault();
        changeDirection(controls[event.key]);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [changeDirection, isGameOver]);

  const cells = Array.from({ length: BOARD_SIZE * BOARD_SIZE }, (_, index) => ({
    x: index % BOARD_SIZE,
    y: Math.floor(index / BOARD_SIZE)
  }));

  return (
    <main className={styles.page}>
      <section className={styles.gameShell} aria-label='彩虹贪吃蛇游戏'>
        <div className={styles.intro}>
          <p className={styles.eyebrow}><span /> PLAYFUL ARCADE</p>
          <h1>彩虹贪吃蛇</h1>
          <p className={styles.subtitle}>吃掉彩虹果实，别撞到边界和自己。</p>
          <div className={styles.difficulty} aria-label='游戏难度：五颗星中的四颗'>
            <span>难度</span><b>★ ★ ★ ★</b><i>★</i>
          </div>
        </div>

        <div className={styles.stats}>
          <div><span>得分</span><strong>{score}</strong></div>
          <div><span>最高分</span><strong>{bestScore}</strong></div>
        </div>

        <div className={styles.boardWrap}>
          <div className={styles.board} role='grid' aria-label='游戏棋盘'>
            {cells.map((cell) => {
              const snakeIndex = snake.findIndex((segment) => isSamePoint(segment, cell));
              const isFood = isSamePoint(food, cell);
              const foodColor = FOOD_COLORS[(food.x + food.y) % FOOD_COLORS.length];
              return (
                <div className={styles.cell} role='gridcell' key={`${cell.x}-${cell.y}`}>
                  {snakeIndex >= 0 && <span className={`${styles.snake} ${snakeIndex === 0 ? styles.head : ''}`} style={{ '--segment': snakeIndex } as CSSProperties} />}
                  {isFood && <span className={styles.food} style={{ '--food': foodColor } as CSSProperties} />}
                </div>
              );
            })}
          </div>
          {!isPlaying && (
            <div className={styles.overlay}>
              <p>{isGameOver ? '本局结束' : '准备出发？'}</p>
              <button type='button' onClick={restart}>{isGameOver ? '再玩一次' : '开始游戏'}</button>
            </div>
          )}
        </div>

        <div className={styles.controls}>
          <button type='button' className={styles.pauseButton} onClick={() => !isGameOver && setIsPlaying((playing) => !playing)} disabled={isGameOver}>
            {isPlaying ? 'Ⅱ 暂停' : '▶ 继续'}
          </button>
          <button type='button' className={styles.restartButton} onClick={restart}>↻ 重新开始</button>
        </div>

        <div className={styles.help}>
          <span>方向键 / WASD 控制</span><span>空格键暂停</span>
        </div>

        <div className={styles.dpad} aria-label='触屏方向控制'>
          <button type='button' onClick={() => changeDirection({ x: 0, y: -1 })}>↑</button>
          <button type='button' onClick={() => changeDirection({ x: -1, y: 0 })}>←</button>
          <button type='button' onClick={() => changeDirection({ x: 0, y: 1 })}>↓</button>
          <button type='button' onClick={() => changeDirection({ x: 1, y: 0 })}>→</button>
        </div>
      </section>
    </main>
  );
}
