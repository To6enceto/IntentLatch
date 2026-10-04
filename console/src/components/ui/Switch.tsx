import type { ComponentProps } from "react";

type SwitchProps = Omit<ComponentProps<"button">, "onChange" | "role"> & {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
};

export function Switch({ checked, onCheckedChange, className, ...props }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={className ? `switch ${className}` : "switch"}
      onClick={() => onCheckedChange(!checked)}
      {...props}
    >
      <span className="switch-thumb" />
    </button>
  );
}
