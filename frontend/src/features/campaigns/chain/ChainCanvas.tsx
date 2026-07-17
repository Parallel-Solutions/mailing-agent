import type { ChainLayout } from './chainUtils';

type Props = {
  layout: ChainLayout;
};

export function ChainCanvas({ layout }: Props) {
  return (
    <svg
      className="chain-canvas-edges"
      width={layout.width}
      height={layout.height}
      aria-hidden
    >
      <defs>
        <marker
          id="chain-arrow"
          markerWidth="8"
          markerHeight="8"
          refX="6"
          refY="4"
          orient="auto"
        >
          <path d="M0,0 L8,4 L0,8 Z" fill="#b0b0b0" />
        </marker>
      </defs>
      {layout.edges.map((edge) =>
        edge.path ? (
          <path
            key={edge.id}
            d={edge.path}
            fill="none"
            stroke="#c8c8c8"
            strokeWidth={1.5}
            markerEnd="url(#chain-arrow)"
          />
        ) : null,
      )}
    </svg>
  );
}
