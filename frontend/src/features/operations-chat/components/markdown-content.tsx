import type { ComponentPropsWithoutRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/utils';

interface MarkdownContentProps {
  content: string;
  className?: string;
}

function MarkdownCode({
  className,
  children,
  ...props
}: ComponentPropsWithoutRef<'code'>): React.ReactNode {
  const isBlock = className?.includes('language-');
  return isBlock ? (
    <code
      className={cn('block overflow-x-auto rounded-md bg-muted px-3 py-2 text-xs', className)}
      {...props}
    >
      {children}
    </code>
  ) : (
    <code className={cn('rounded bg-muted px-1 py-0.5 text-[0.85em]', className)} {...props}>
      {children}
    </code>
  );
}

export function MarkdownContent({ content, className }: MarkdownContentProps): React.ReactNode {
  return (
    <div className={cn('space-y-3 break-words leading-relaxed', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className='text-xl font-semibold'>{children}</h1>,
          h2: ({ children }) => <h2 className='text-lg font-semibold'>{children}</h2>,
          h3: ({ children }) => <h3 className='text-base font-semibold'>{children}</h3>,
          p: ({ children }) => <p>{children}</p>,
          ul: ({ children }) => <ul className='list-disc space-y-1 pl-5'>{children}</ul>,
          ol: ({ children }) => <ol className='list-decimal space-y-1 pl-5'>{children}</ol>,
          li: ({ children }) => <li>{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className='border-l-2 border-muted-foreground/40 pl-3 text-muted-foreground'>
              {children}
            </blockquote>
          ),
          a: ({ children, href }) => (
            <a
              className='text-primary underline underline-offset-2'
              href={href}
              rel='noreferrer'
              target='_blank'
            >
              {children}
            </a>
          ),
          pre: ({ children }) => <pre className='my-2 overflow-x-auto'>{children}</pre>,
          code: MarkdownCode,
          table: ({ children }) => (
            <div className='my-3 overflow-x-auto'>
              <table className='w-full border-collapse text-sm'>{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className='border bg-muted px-2 py-1 text-left font-semibold'>{children}</th>
          ),
          td: ({ children }) => <td className='border px-2 py-1 align-top'>{children}</td>,
          hr: () => <hr className='border-border' />
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
