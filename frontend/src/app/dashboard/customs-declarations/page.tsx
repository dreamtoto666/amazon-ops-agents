import PageContainer from '@/components/layout/page-container';
import { CustomsDeclarationWorkbench } from '@/features/customs-declarations/components/customs-declaration-workbench';

export const metadata = {
  title: '报关单填写'
};

export default function CustomsDeclarationsPage() {
  return (
    <PageContainer
      pageTitle='报关单填写'
      pageDescription='按普船/快船和 0110/9810 自动分类、汇总并填写报关单，全程使用固定规则。'
    >
      <CustomsDeclarationWorkbench />
    </PageContainer>
  );
}
