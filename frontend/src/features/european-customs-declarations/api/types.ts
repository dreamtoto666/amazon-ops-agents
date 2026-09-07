export interface EuropeanCustomsIssue {
  code: string;
  message: string;
  file: string | null;
  sheet: string | null;
  row: number | null;
  field: string | null;
}

export interface EuropeanCustomsCategoryPreview {
  channel: '华贸卡航' | '华贸海运' | '华贸空运' | '一八海运';
  prefix: '红福' | '其它';
  country: '德国' | '法国' | '英国';
  trade_mode: '0110' | '9810';
  shipment_count: number;
  item_count: number;
  box_count: number;
  net_weight: string;
  gross_weight: string;
  amount: string;
  generates_file: boolean;
}

export interface EuropeanCustomsPreview {
  declaration_date: string;
  can_generate: boolean;
  categories: EuropeanCustomsCategoryPreview[];
  warnings: EuropeanCustomsIssue[];
  errors: EuropeanCustomsIssue[];
}
