import React from 'react';
import { cn } from '../../utils/cn';

interface SwitchProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

export const Switch = React.forwardRef<HTMLInputElement, SwitchProps>(
  ({ className, label, ...props }, ref) => {
    return (
      <label className="flex items-center gap-3 cursor-pointer">
        <input
          ref={ref}
          type="checkbox"
          className={cn(
            'h-5 w-5 appearance-none rounded-full border-2 transition-colors',
            'bg-gray-300 checked:bg-purple-600',
            'focus:outline-none focus:ring-2 focus:ring-purple-500 focus:ring-offset-2',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            props.className
          )}
          {...props}
          ref={ref}
        />
        {props.label && <span className="text-sm text-gray-700">{props.label}</span>}
      </label>
    );
  }
);

Switch.displayName = 'Switch';
