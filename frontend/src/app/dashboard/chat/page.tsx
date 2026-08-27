import PageContainer from '@/components/layout/page-container';
import { OperationsChat } from '@/features/operations-chat/components/operations-chat';

export const metadata = {
  title: 'AI 运营助手'
};

export default function ChatPage() {
  return (
    <PageContainer
      pageTitle='AI 运营助手'
      pageDescription='用自然语言创建真实总控 Agent 任务，并在对话中查看阶段进度和结果。'
    >
      <OperationsChat />
    </PageContainer>
  );
}
