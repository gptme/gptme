/**
 * @param   {Token} type
 * @returns {string    } */
export function token_to_string(type: Token): string;
/**
 * @param   {Attr} type
 * @returns {string    } */
export function attr_to_html_attr(type: Attr): string;
/**
 * Makes a new Parser object.
 * @param   {Any_Renderer} renderer
 * @returns {Parser      } */
export function parser(renderer: Any_Renderer): Parser;
/**
 * Finish rendering the markdown - flushes any remaining text.
 * @param   {Parser} p
 * @returns {void  } */
export function parser_end(p: Parser): void;
/**
 * Parse and render another chunk of markdown.
 * @param   {Parser} p
 * @param   {string} chunk
 * @returns {void  } */
export function parser_write(p: Parser, chunk: string): void;
/**
 * @template T
 * @callback Renderer_Add_Token
 * @param   {T    } data
 * @param   {Token} type
 * @returns {void } */
/**
 * @template T
 * @callback Renderer_End_Token
 * @param   {T    } data
 * @returns {void } */
/**
 * @template T
 * @callback Renderer_Add_Text
 * @param   {T     } data
 * @param   {string} text
 * @returns {void  } */
/**
 * @template T
 * @callback Renderer_Set_Attr
 * @param   {T     } data
 * @param   {Attr  } type
 * @param   {string} value
 * @returns {void  } */
/**
 * The renderer interface.
 * @template T
 * @typedef  {object               } Renderer
 * @property {T                    } data      User data object. Available as first param in callbacks.
 * @property {Renderer_Add_Token<T>} add_token When the tokens starts.
 * @property {Renderer_End_Token<T>} end_token When the token ends.
 * @property {Renderer_Add_Text <T>} add_text  To append text to current token. Can be called multiple times or none.
 * @property {Renderer_Set_Attr <T>} set_attr  Set additional attributes of current token eg. the link url.
 */
/** @typedef {Renderer<any>} Any_Renderer */
/**
 * @typedef  {object} Default_Renderer_Data
 * @property {HTMLElement[]} nodes
 * @property {number       } index
 *
 * @typedef {Renderer          <Default_Renderer_Data>} Default_Renderer
 * @typedef {Renderer_Add_Token<Default_Renderer_Data>} Default_Renderer_Add_Token
 * @typedef {Renderer_End_Token<Default_Renderer_Data>} Default_Renderer_End_Token
 * @typedef {Renderer_Add_Text <Default_Renderer_Data>} Default_Renderer_Add_Text
 * @typedef {Renderer_Set_Attr <Default_Renderer_Data>} Default_Renderer_Set_Attr
 */
/**
 * @param   {HTMLElement     } root
 * @returns {Default_Renderer} */
export function default_renderer(root: HTMLElement): Default_Renderer;
export function default_add_token(data: Default_Renderer_Data, type: Token): void;
export function default_end_token(data: Default_Renderer_Data): void;
export function default_add_text(data: Default_Renderer_Data, text: string): void;
export function default_set_attr(data: Default_Renderer_Data, type: Attr, value: string): void;
/**
 * @typedef {undefined} Logger_Renderer_Data
 *
 * @typedef {Renderer          <Logger_Renderer_Data>} Logger_Renderer
 * @typedef {Renderer_Add_Token<Logger_Renderer_Data>} Logger_Renderer_Add_Token
 * @typedef {Renderer_End_Token<Logger_Renderer_Data>} Logger_Renderer_End_Token
 * @typedef {Renderer_Add_Text <Logger_Renderer_Data>} Logger_Renderer_Add_Text
 * @typedef {Renderer_Set_Attr <Logger_Renderer_Data>} Logger_Renderer_Set_Attr
 */
/** @returns {Logger_Renderer} */
export function logger_renderer(): Logger_Renderer;
export function logger_add_token(data: undefined, type: Token): void;
export function logger_end_token(data: undefined): void;
export function logger_add_text(data: undefined, text: string): void;
export function logger_set_attr(data: undefined, type: Attr, value: string): void;
export const DOCUMENT: 1;
export const PARAGRAPH: 2;
export const HEADING_1: 3;
export const HEADING_2: 4;
export const HEADING_3: 5;
export const HEADING_4: 6;
export const HEADING_5: 7;
export const HEADING_6: 8;
export const CODE_BLOCK: 9;
export const CODE_FENCE: 10;
export const CODE_INLINE: 11;
export const ITALIC_AST: 12;
export const ITALIC_UND: 13;
export const STRONG_AST: 14;
export const STRONG_UND: 15;
export const STRIKE: 16;
export const LINK: 17;
export const RAW_URL: 18;
export const IMAGE: 19;
export const BLOCKQUOTE: 20;
export const LINE_BREAK: 21;
export const RULE: 22;
export const LIST_UNORDERED: 23;
export const LIST_ORDERED: 24;
export const LIST_ITEM: 25;
export const CHECKBOX: 26;
export const TABLE: 27;
export const TABLE_ROW: 28;
export const TABLE_CELL: 29;
export const MAYBE_URL: 30;
export const MAYBE_TASK: 31;
export const EQUATION_BLOCK: 32;
export const EQUATION_INLINE: 33;
export type Token = (typeof Token)[keyof typeof Token];
export namespace Token {
  export { DOCUMENT as Document };
  export { BLOCKQUOTE as Blockquote };
  export { PARAGRAPH as Paragraph };
  export { HEADING_1 as Heading_1 };
  export { HEADING_2 as Heading_2 };
  export { HEADING_3 as Heading_3 };
  export { HEADING_4 as Heading_4 };
  export { HEADING_5 as Heading_5 };
  export { HEADING_6 as Heading_6 };
  export { CODE_BLOCK as Code_Block };
  export { CODE_FENCE as Code_Fence };
  export { CODE_INLINE as Code_Inline };
  export { ITALIC_AST as Italic_Ast };
  export { ITALIC_UND as Italic_Und };
  export { STRONG_AST as Strong_Ast };
  export { STRONG_UND as Strong_Und };
  export { STRIKE as Strike };
  export { LINK as Link };
  export { RAW_URL as Raw_URL };
  export { IMAGE as Image };
  export { LINE_BREAK as Line_Break };
  export { RULE as Rule };
  export { LIST_UNORDERED as List_Unordered };
  export { LIST_ORDERED as List_Ordered };
  export { LIST_ITEM as List_Item };
  export { CHECKBOX as Checkbox };
  export { TABLE as Table };
  export { TABLE_ROW as Table_Row };
  export { TABLE_CELL as Table_Cell };
  export { EQUATION_BLOCK as Equation_Block };
  export { EQUATION_INLINE as Equation_Inline };
}
export const HREF: 1;
export const SRC: 2;
export const LANG: 4;
export const CHECKED: 8;
export const START: 16;
export type Attr = (typeof Attr)[keyof typeof Attr];
export namespace Attr {
  export { HREF as Href };
  export { SRC as Src };
  export { LANG as Lang };
  export { CHECKED as Checked };
  export { START as Start };
}
export type Renderer_Add_Token<T> = (data: T, type: Token) => void;
export type Renderer_End_Token<T> = (data: T) => void;
export type Renderer_Add_Text<T> = (data: T, text: string) => void;
export type Renderer_Set_Attr<T> = (data: T, type: Attr, value: string) => void;
/**
 * The renderer interface.
 */
export type Renderer<T> = {
  /**
   * User data object. Available as first param in callbacks.
   */
  data: T;
  /**
   * When the tokens starts.
   */
  add_token: Renderer_Add_Token<T>;
  /**
   * When the token ends.
   */
  end_token: Renderer_End_Token<T>;
  /**
   * To append text to current token. Can be called multiple times or none.
   */
  add_text: Renderer_Add_Text<T>;
  /**
   * Set additional attributes of current token eg. the link url.
   */
  set_attr: Renderer_Set_Attr<T>;
};
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Any_Renderer = Renderer<any>;
export type Default_Renderer_Data = {
  nodes: HTMLElement[];
  index: number;
};
export type Default_Renderer = Renderer<Default_Renderer_Data>;
export type Default_Renderer_Add_Token = Renderer_Add_Token<Default_Renderer_Data>;
export type Default_Renderer_End_Token = Renderer_End_Token<Default_Renderer_Data>;
export type Default_Renderer_Add_Text = Renderer_Add_Text<Default_Renderer_Data>;
export type Default_Renderer_Set_Attr = Renderer_Set_Attr<Default_Renderer_Data>;
export type Logger_Renderer_Data = undefined;
export type Logger_Renderer = Renderer<Logger_Renderer_Data>;
export type Logger_Renderer_Add_Token = Renderer_Add_Token<Logger_Renderer_Data>;
export type Logger_Renderer_End_Token = Renderer_End_Token<Logger_Renderer_Data>;
export type Logger_Renderer_Add_Text = Renderer_Add_Text<Logger_Renderer_Data>;
export type Logger_Renderer_Set_Attr = Renderer_Set_Attr<Logger_Renderer_Data>;
export type Parser = {
  /**
   * - {@link Renderer} interface
   */
  renderer: Any_Renderer;
  /**
   * - Text to be added to the last token in the next flush
   */
  text: string;
  /**
   * - Characters for identifying tokens
   */
  pending: string;
  /**
   * - Current token and it's parents (a slice of a tree)
   */
  tokens: Uint32Array;
  /**
   * - Number of tokens in types without root
   */
  len: number;
  /**
   * - Last token in the tree
   */
  token: number;
  spaces: Uint8Array;
  indent: string;
  indent_len: number;
  /**
   * - For {@link Token.Code_Fence} parsing
   */
  code_fence_body: 0 | 1;
  backticks_count: number;
  /**
   * - For Blockquote parsing
   */
  blockquote_idx: number;
  /**
   * - For horizontal rule parsing
   */
  hr_char: string;
  /**
   * - For horizontal rule parsing
   */
  hr_chars: number;
  table_state: number;
};
//# sourceMappingURL=smd.d.ts.map
