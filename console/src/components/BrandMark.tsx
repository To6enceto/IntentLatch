import { Icon } from "./Icon";

export function BrandMark({ large = false }: { large?: boolean }) {
  return (
    <span className={`inline-flex shrink-0 items-center justify-center bg-primary text-primary-foreground ${large ? "size-10 rounded-[10px]" : "size-7 rounded-lg"}`}>
      <Icon name="shield" className={large ? "size-6" : "size-[18px]"} />
    </span>
  );
}
