// Fallback geography for the placeholder grid map (VIS-1) and district names when the TopoJSON is missing.
// Codes are HDX COD-AB v03 ADM2 P-codes; centroids are approximate and used only to lay out grid tiles.
// The real map comes from data/bd_districts.topo.json, which always wins when it is present.

export const FALLBACK_DISTRICTS = [
  ["BD1004", "Barguna", 22.15, 90.12], ["BD1006", "Barishal", 22.70, 90.37], ["BD1009", "Bhola", 22.35, 90.73],
  ["BD1042", "Jhalokati", 22.57, 90.20], ["BD1078", "Patuakhali", 22.25, 90.43], ["BD1079", "Pirojpur", 22.58, 89.97],
  ["BD2003", "Bandarban", 21.80, 92.35], ["BD2012", "Brahmanbaria", 23.95, 91.10], ["BD2013", "Chandpur", 23.23, 90.70],
  ["BD2015", "Chattogram", 22.50, 91.90], ["BD2019", "Cumilla", 23.45, 91.18], ["BD2022", "Cox's Bazar", 21.45, 92.00],
  ["BD2030", "Feni", 22.95, 91.40], ["BD2046", "Khagrachhari", 23.12, 91.95], ["BD2051", "Lakshmipur", 22.95, 90.83],
  ["BD2075", "Noakhali", 22.82, 91.10], ["BD2084", "Rangamati", 22.75, 92.25], ["BD3026", "Dhaka", 23.80, 90.30],
  ["BD3029", "Faridpur", 23.55, 89.80], ["BD3033", "Gazipur", 24.05, 90.43], ["BD3035", "Gopalganj", 23.00, 89.90],
  ["BD3048", "Kishoreganj", 24.40, 90.90], ["BD3054", "Madaripur", 23.20, 90.15], ["BD3056", "Manikganj", 23.85, 89.95],
  ["BD3059", "Munshiganj", 23.48, 90.45], ["BD3067", "Narayanganj", 23.70, 90.55], ["BD3068", "Narsingdi", 24.00, 90.75],
  ["BD3082", "Rajbari", 23.70, 89.60], ["BD3086", "Shariatpur", 23.20, 90.40], ["BD3093", "Tangail", 24.30, 89.95],
  ["BD4001", "Bagerhat", 22.40, 89.75], ["BD4018", "Chuadanga", 23.62, 88.80], ["BD4041", "Jashore", 23.15, 89.20],
  ["BD4044", "Jhenaidah", 23.50, 89.10], ["BD4047", "Khulna", 22.55, 89.45], ["BD4050", "Kushtia", 23.85, 89.05],
  ["BD4055", "Magura", 23.45, 89.40], ["BD4057", "Meherpur", 23.78, 88.65], ["BD4065", "Narail", 23.10, 89.55],
  ["BD4087", "Satkhira", 22.35, 89.10], ["BD4539", "Jamalpur", 25.00, 89.85], ["BD4561", "Mymensingh", 24.75, 90.40],
  ["BD4572", "Netrakona", 24.85, 90.85], ["BD4589", "Sherpur", 25.10, 90.05], ["BD5010", "Bogura", 24.85, 89.37],
  ["BD5038", "Joypurhat", 25.10, 89.05], ["BD5064", "Naogaon", 24.85, 88.75], ["BD5069", "Natore", 24.40, 89.10],
  ["BD5070", "Chapainawabganj", 24.70, 88.25], ["BD5076", "Pabna", 24.00, 89.35], ["BD5081", "Rajshahi", 24.40, 88.60],
  ["BD5088", "Sirajganj", 24.40, 89.65], ["BD5527", "Dinajpur", 25.60, 88.65], ["BD5532", "Gaibandha", 25.30, 89.55],
  ["BD5549", "Kurigram", 25.80, 89.65], ["BD5552", "Lalmonirhat", 26.00, 89.30], ["BD5573", "Nilphamari", 26.00, 88.95],
  ["BD5577", "Panchagarh", 26.30, 88.55], ["BD5585", "Rangpur", 25.70, 89.25], ["BD5594", "Thakurgaon", 26.00, 88.30],
  ["BD6036", "Habiganj", 24.35, 91.40], ["BD6058", "Moulvibazar", 24.45, 91.80], ["BD6090", "Sunamganj", 25.05, 91.30],
  ["BD6091", "Sylhet", 24.90, 91.90],
].map(([code, name, lat, lon]) => ({ code, name, lat, lon }));

// Snap centroids to a tile grid; collisions move to the nearest free cell.
export function gridLayout(codes) {
  const byCode = new Map(FALLBACK_DISTRICTS.map((d) => [d.code, d]));
  const taken = new Map();
  const out = new Map();
  let spare = 0;
  for (const code of codes) {
    const d = byCode.get(code);
    let col; let row;
    if (d) { col = Math.round((d.lon - 88.2) / 0.36); row = Math.round((26.4 - d.lat) / 0.33); }
    else { col = 12 + (spare % 3); row = 14 + Math.floor(spare / 3); spare += 1; }
    let best = null;
    for (let r = 0; r < 6 && !best; r += 1) {
      for (let dc = -r; dc <= r && !best; dc += 1) {
        for (let dr = -r; dr <= r && !best; dr += 1) {
          const k = `${col + dc},${row + dr}`;
          if (!taken.has(k)) best = [col + dc, row + dr];
        }
      }
    }
    taken.set(`${best[0]},${best[1]}`, code);
    out.set(code, { col: best[0], row: best[1] });
  }
  return out;
}
