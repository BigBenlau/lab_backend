import type { Content, LabDataset } from './models.ts';

// Entire fixture is illustrative. Do not publish these people or papers as fact.
const common = {
  publishState: 'draft', isDemo: true,
  createdAt: '2026-11-04T09:00:00Z', updatedAt: '2026-11-04T09:00:00Z', publishedAt: null,
} satisfies Omit<Content, 'id' | 'slug'>;

export default {
  schemaVersion: '1.0',
  assets: [
    { id: 'asset-logo', url: 'https://example.org/assets/casino-logo.svg', mimeType: 'image/svg+xml', alt: 'CASINO Lab', caption: null, credit: null, width: 1200, height: 1018 },
    { id: 'asset-group', url: 'https://example.org/assets/group-photo.png', mimeType: 'image/png', alt: 'CASINO lab members together', caption: 'Our group', credit: 'CASINO Lab', width: 1177, height: 662 },
  ],
  researchAreas: [{
    ...common, id: 'area-privacy', slug: 'usable-security-privacy',
    title: 'Usable Security & Privacy', summary: 'Understanding everyday privacy decisions.',
    bodyMarkdown: 'Research description to be supplied by the lab.',
    questions: ['How do people understand privacy choices?'], tags: ['Privacy', 'Human factors'],
    coverAssetId: null, sortOrder: 1,
  }],
  people: [
    {
      ...common, id: 'person-alex', slug: 'alex-example', name: 'Alex Example', familyName: 'Example', preferredName: null,
      title: 'Doctoral researcher', affiliation: 'Example University', portraitAssetId: null,
      bioMarkdown: 'Sample profile for API integration.', researchAreaIds: ['area-privacy'], interests: ['Privacy'],
      memberships: [
        { role: 'research_assistant', isCurrent: false, startedOn: '2024-09-01', endedOn: '2025-08-31' },
        { role: 'phd_student', isCurrent: true, startedOn: '2025-09-01', endedOn: null },
      ],
      state: 'member', alumniSince: null, alumniDestination: null, publicEmail: null, links: [{ label: 'Website', url: 'https://example.org/alex' }], sortOrder: 1,
    },
    {
      ...common, id: 'person-sam', slug: 'sam-example', name: 'Sam Example', familyName: 'Example', preferredName: null,
      title: null, affiliation: 'Example Research Institute', portraitAssetId: null,
      bioMarkdown: 'Sample former member.', researchAreaIds: ['area-privacy'], interests: ['Security'],
      memberships: [{ role: 'phd_student', isCurrent: false, startedOn: null, endedOn: null }],
      state: 'alumni', alumniSince: '2026-06', alumniDestination: 'Example Research Institute', publicEmail: null, links: [], sortOrder: 2,
    },
  ],
  publications: [{
    ...common, id: 'pub-example', slug: 'example-everyday-privacy', title: '[Sample] Everyday privacy decisions',
    pageUrl: '#publications/pub-example',
    authors: [
      { personId: 'person-alex', name: 'Alex Example', affiliation: 'Example University', isCorresponding: true, equalContribution: false },
      { personId: null, name: 'External Example', affiliation: 'Another University', isCorresponding: false, equalContribution: false },
      { personId: 'person-sam', name: 'Sam Example', affiliation: 'Example Research Institute', isCorresponding: false, equalContribution: false },
    ],
    year: 2026, publicationDate: null, type: 'conference', publicationStatus: 'accepted',
    venue: { name: 'Example Conference', shortName: null }, abstract: 'Sample abstract for integration testing, not a real publication.',
    researchAreaIds: ['area-privacy'], keywords: ['Privacy'], thumbnailAssetId: null,
    doi: null, pdfAssetId: null, pdfUrl: null, projectUrl: null, codeUrl: null, videoUrl: null,
    volume: null, issue: null, pages: null, awards: [], bibtex: null, featured: false,
  }],
  news: [{
    ...common, id: 'news-example', slug: 'example-seminar', title: '[Sample] Research seminar', category: 'event',
    summary: 'Example seminar entry.', bodyMarkdown: 'Event details go here.', date: '2026-11-04',
    coverAssetId: null, galleryAssetIds: [], relatedPersonIds: ['person-alex'], relatedPublicationIds: ['pub-example'],
    event: { startsAt: '2026-12-01T15:00:00+08:00', endsAt: '2026-12-01T16:00:00+08:00', timeZone: 'Asia/Macau', location: 'Room to be confirmed', registrationUrl: null },
    pinned: false,
  }],
  opportunities: [{
    ...common, id: 'opening-example', slug: 'example-phd-opening', title: '[Sample] PhD opportunity', kind: 'phd', availability: 'open',
    summary: 'Example recruitment entry.', bodyMarkdown: 'Replace with approved recruitment information.',
    requirements: ['Replace with actual eligibility criteria.'], applicationChecklist: ['CV', 'Research interests'],
    deadlineAt: null, contactEmail: 'lab@example.org', applicationUrl: null, guidanceUrl: null,
    researchAreaIds: ['area-privacy'], sortOrder: 1,
  }],
  siteSettings: {
    name: 'CASINO Lab', fullName: 'Collaborative Security and Interaction Group', institution: 'University of Macau',
    introduction: 'Lab introduction to be supplied.', logoAssetId: 'asset-logo', darkLogoAssetId: null,
    heroPhotoAssetIds: ['asset-group'], publicEmail: null, address: null, links: [], featuredPublicationIds: [],
    updatedAt: '2026-11-04T09:00:00Z',
  },
} satisfies LabDataset;
