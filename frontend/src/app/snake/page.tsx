import SnakeGame from './snake-game';

export const metadata = {
  title: '彩虹贪吃蛇',
  description: '一款彩色的贪吃蛇小游戏，难度为四星。'
};

export default function SnakePage() {
  return <SnakeGame />;
}
