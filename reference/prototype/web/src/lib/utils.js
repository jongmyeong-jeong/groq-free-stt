import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

// shadcn/ui convention: classnames merger used across components.
export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
