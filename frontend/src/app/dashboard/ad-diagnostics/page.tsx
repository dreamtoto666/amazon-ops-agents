import PageContainer from '@/components/layout/page-container';
import { AdvertisingDiagnosticsWorkbench } from '@/features/advertising-diagnostics/components/advertising-diagnostics-workbench';

export const metadata = {
  title: '广告异常诊断'
};

export default function AdvertisingDiagnosticsPage() {
  return (
    <PageContainer
      pageTitle='广告异常诊断与运营代办'
      pageDescription='定时或手动巡检广告表现，通过四个专业 Agent 生成可追溯的异常诊断和运营代办。'
    >
      <AdvertisingDiagnosticsWorkbench />
    </PageContainer>
  );
}
