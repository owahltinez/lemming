window.updateFavicon = (state) => {
  const emoji = "🐹";
  let overlay = "";

  // Badge position: Bottom-Right (75, 75)
  const bx = 75;
  const by = 75;

  if (state === "running") {
    overlay = `
      <circle cx="${bx}" cy="${by}" r="30" fill="white" />
      <circle cx="${bx}" cy="${by}" r="22" fill="#1e40af">
        <animate attributeName="r" values="16;26;16" dur="1s" repeatCount="indefinite" />
        <animate attributeName="fill-opacity" values="0.6;1;0.6" dur="1s" repeatCount="indefinite" />
      </circle>
    `;
  } else if (state === "error") {
    overlay = `
      <circle cx="${bx}" cy="${by}" r="30" fill="white" />
      <circle cx="${bx}" cy="${by}" r="24" fill="#9f1239" />
      <path d="M${bx - 10} ${by - 10} l20 20 M${bx + 10} ${by - 10} l-20 20" stroke="white" stroke-width="10" stroke-linecap="round" />
    `;
  } else if (state === "success") {
    overlay = `
      <circle cx="${bx}" cy="${by}" r="30" fill="white" />
      <circle cx="${bx}" cy="${by}" r="24" fill="#065f46" />
      <path d="M${bx - 12} ${by} l8 8 l16 -16" stroke="white" stroke-width="10" stroke-linecap="round" stroke-linejoin="round" fill="none" />
    `;
  }

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
    <text y=".9em" font-size="90">${emoji}</text>
    ${overlay}
  </svg>`;

  const link = document.querySelector('link[rel="icon"]');
  if (link) {
    link.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  }
};
