/**
 * Landing-page content you can edit without touching any component.
 * Leave a string empty ('') and the landing page shows a clearly marked placeholder.
 * Images live under frontend/public/ (e.g. '/branding/...').
 *
 * Source: the team's page on spaceappschallenge.org (2026 / find-a-team / pizzaneptune).
 * Contact details (emails, phone) from that page are deliberately NOT included here.
 */
export const PROJECT = {
  name: 'Spark Seer',

  challenge: {
    event: 'NASA Space Apps Challenge 2026',
    title: 'Harmonization of MODIS and VIIRS Hot Spots',
    // Our approach (the official challenge text is on the challenge page linked below).
    summary:
      'Our answer is a harmonized MODIS + VIIRS burning-activity calendar for Tunisia and northern Algeria: a consistent hotspot record across both sensors, with anomaly flags, a held-out validation of the harmonization, and sourced evidence for any month.',
    url: 'https://www.spaceappschallenge.org/2026/challenges/modis-and-viirs-hot-spots/',
    logo: '/branding/space-apps-challenge-2026.png',
  },

  team: {
    name: 'PizzaNeptune',
    logo: '/branding/pizzaneptune-team.jpg',
    url: 'https://www.spaceappschallenge.org/2026/find-a-team/pizzaneptune/',
    tagline: 'Tunis, Tunisia · NASA Space Apps Challenge 2026',
    members: [
      // photo / github / linkedin are optional (photo: path under /public, e.g. '/team/jane.jpg')
      { name: 'Sarra Mansour', role: 'Team owner', photo: '', github: '', linkedin: '' },
      { name: 'Wejden Ghabarou', role: 'Team member', photo: '', github: '', linkedin: '' },
      { name: 'Mohamed ZERAI', role: 'Team member', photo: '', github: '', linkedin: '' },
      { name: 'Mohamed Nidhal Sadfi', role: 'Team member', photo: '', github: '', linkedin: '' },
      { name: 'Omar Kthiri', role: 'Team member', photo: '', github: '', linkedin: '' },
      { name: 'Mohamed Fathallah', role: 'Team member', photo: '', github: '', linkedin: '' },
    ],
  },
}
