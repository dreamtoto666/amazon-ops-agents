import PageContainer from '@/components/layout/page-container';
import { EuropeanCustomsDeclarationWorkbench } from '@/features/european-customs-declarations/components/european-customs-declaration-workbench';

export const metadata = { title: '欧洲报关表填写' };

export default function EuropeanCustomsDeclarationsPage() {
  return <PageContainer pageTitle='欧洲报关表填写' pageDescription='仅读取 Sheet1，按渠道、红福/其它和国家自动拆分并生成欧洲报关表。'><EuropeanCustomsDeclarationWorkbench /></PageContainer>;
}
