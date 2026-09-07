export interface CustomsIssue {
  code: string;
  message: string;
  file: string | null;
  sheet: string | null;
  row: number | null;
  field: string | null;
}

export interface CustomsPreviewItem {
  rule_id: string;
  declaration_name: string;
  model: string;
  material: string;
  hs_code: string;
  quantity: string;
  unit_weight: string;
  net_weight: string;
  gross_weight: string;
  unit_price: string;
  amount: string;
}

export interface CustomsCategoryPreview {
  shipping_speed: '普船统配' | '快船' | '美森极致达' | '普船特惠';
  trade_mode: '0110' | '9810';
  order_count: number;
  shipment_count: number;
  item_count: number;
  box_count: number;
  net_weight: string;
  gross_weight: string;
  amount: string;
  generates_file: boolean;
  items: CustomsPreviewItem[];
}

export interface CustomsPreview {
  declaration_date: string;
  can_generate: boolean;
  categories: CustomsCategoryPreview[];
  warnings: CustomsIssue[];
  errors: CustomsIssue[];
}
