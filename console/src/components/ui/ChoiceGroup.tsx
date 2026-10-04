import type { ReactNode } from "react";

type ChoiceGroupProps = {
  legend: string;
  name: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string; hint?: ReactNode }[];
  columns?: 2 | 3;
  compact?: boolean;
  disabled?: boolean;
};

/** Radio options shown as cards, in a fieldset with a visible legend. */
export function ChoiceGroup({ legend, name, value, onChange, options, columns = 2, compact, disabled }: ChoiceGroupProps) {
  return (
    <fieldset className="field" disabled={disabled}>
      <legend>{legend}</legend>
      <div className={`choice-grid cols-${columns}${compact ? " is-compact" : ""}`}>
        {options.map((option) => (
          <label key={option.value} className="choice-card">
            <input type="radio" name={name} value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} />
            <span>
              <span className="choice-label">{option.label}</span>
              {option.hint && <span className="choice-hint">{option.hint}</span>}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
