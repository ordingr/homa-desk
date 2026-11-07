const base = {
  width: 16,
  height: 16,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

export const LocalIcon = (props) => (
  <svg {...base} {...props}>
    <path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6l-7-3Z" />
    <path d="m9 12 2 2 4-4" />
  </svg>
);

export const CloudSendIcon = (props) => (
  <svg {...base} {...props}>
    <path d="M7 18a4 4 0 0 1-.5-7.97A5.5 5.5 0 0 1 17 8.5 4.5 4.5 0 0 1 17 18" />
    <path d="M12 21v-7" />
    <path d="m9 16 3-3 3 3" />
  </svg>
);

export const CloudOffIcon = (props) => (
  <svg {...base} {...props}>
    <path d="M7 18a4 4 0 0 1-.5-7.97A5.5 5.5 0 0 1 17 8.5 4.5 4.5 0 0 1 17 18Z" />
  </svg>
);

export const SunIcon = (props) => (
  <svg {...base} {...props}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
);

export const MoonIcon = (props) => (
  <svg {...base} {...props}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z" />
  </svg>
);

export const CloseIcon = (props) => (
  <svg {...base} {...props}>
    <path d="m6 6 12 12M18 6 6 18" />
  </svg>
);

export const CopyIcon = (props) => (
  <svg {...base} {...props}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V6a2 2 0 0 1 2-2h8" />
  </svg>
);

export const SearchIcon = (props) => (
  <svg {...base} {...props}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m16 16 4.5 4.5" />
  </svg>
);

export const StarIcon = (props) => (
  <svg {...base} {...props}>
    <path d="m12 3 2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7L12 3Z" />
  </svg>
);
