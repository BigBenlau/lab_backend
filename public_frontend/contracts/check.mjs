import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import fixture from './example.ts';

// Fixture integrity checks; production request validation belongs to the backend.
function validate(data) {
  const names = ['people', 'researchAreas', 'publications', 'news', 'opportunities', 'assets'];
  const sets = Object.fromEntries(names.map(name => [name, new Set(data[name].map(item => item.id))]));
  const unique = values => assert.equal(new Set(values).size, values.length, 'Duplicate values');
  const ref = (id, collection) => assert(id === null || sets[collection].has(id), `Unknown ${collection}: ${id}`);
  const refs = (ids, collection) => { unique(ids); ids.forEach(id => ref(id, collection)); };
  const date = value => {
    if (value === null) return;
    assert(/^\d{4}-\d{2}-\d{2}$/.test(value), 'Invalid date format');
    assert.equal(new Date(value).toISOString().slice(0, 10), value, 'Invalid calendar date');
  };
  const instant = value => {
    assert(/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)), 'Invalid timestamp');
  };
  for (const name of names) {
    unique(data[name].map(item => item.id));
    if (name === 'assets') continue;
    unique(data[name].map(item => item.slug));
    for (const item of data[name]) {
      assert(/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(item.slug), 'Invalid slug');
      instant(item.createdAt); instant(item.updatedAt);
      if (item.publishedAt !== null) instant(item.publishedAt);
      if (item.publishState === 'published') assert(item.publishedAt, 'Missing publishedAt');
    }
  }
  for (const person of data.people) {
    assert(person.name.trim() && person.familyName.trim());
    assert(['member', 'alumni'].includes(person.state));
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(person.alumniSince ?? '')) date(person.alumniSince);
    assert(person.state === 'alumni' || person.alumniSince === null);
    assert(person.memberships.length, 'Missing membership');
    unique(person.memberships.filter(m => m.isCurrent).map(m => m.role));
    for (const m of person.memberships) {
      date(m.startedOn); date(m.endedOn);
      assert(!m.isCurrent || m.endedOn === null, 'Current membership has ended');
      assert(!m.startedOn || !m.endedOn || m.endedOn >= m.startedOn, 'Reversed membership dates');
    }
    refs(person.researchAreaIds, 'researchAreas'); ref(person.portraitAssetId, 'assets');
  }
  unique(data.publications.filter(p => p.doi).map(p => p.doi.toLowerCase()));
  for (const paper of data.publications) {
    assert(paper.title.trim()); assert(paper.authors.length, 'Missing authors');
    unique(paper.authors.filter(a => a.personId !== null).map(a => a.personId));
    for (const author of paper.authors) { assert(author.name.trim()); ref(author.personId, 'people'); }
    assert(Number.isInteger(paper.year) && paper.year >= 1000 && paper.year <= 9999, 'Invalid year');
    date(paper.publicationDate);
    if (paper.publicationDate) assert.equal(Number(paper.publicationDate.slice(0, 4)), paper.year);
    assert(!(paper.pdfAssetId && paper.pdfUrl), 'Ambiguous PDF source');
    refs(paper.researchAreaIds, 'researchAreas');
    ref(paper.thumbnailAssetId, 'assets'); ref(paper.pdfAssetId, 'assets');
    if (paper.pdfAssetId) assert.equal(data.assets.find(a => a.id === paper.pdfAssetId).mimeType, 'application/pdf');
  }
  for (const area of data.researchAreas) ref(area.coverAssetId, 'assets');
  for (const news of data.news) {
    date(news.date); ref(news.coverAssetId, 'assets'); refs(news.galleryAssetIds, 'assets');
    refs(news.relatedPersonIds, 'people'); refs(news.relatedPublicationIds, 'publications');
    assert.equal(news.category === 'event', news.event !== null, 'Event category mismatch');
    if (news.event) {
      instant(news.event.startsAt);
      new Intl.DateTimeFormat('en', { timeZone: news.event.timeZone });
      if (news.event.endsAt !== null) {
        instant(news.event.endsAt);
        assert(Date.parse(news.event.endsAt) >= Date.parse(news.event.startsAt), 'Reversed event times');
      }
    }
  }
  for (const opportunity of data.opportunities) {
    refs(opportunity.researchAreaIds, 'researchAreas');
    if (opportunity.deadlineAt) instant(opportunity.deadlineAt);
    if (opportunity.availability === 'open') assert(opportunity.contactEmail || opportunity.applicationUrl, 'Missing application channel');
  }
  const site = data.siteSettings;
  ref(site.logoAssetId, 'assets'); ref(site.darkLogoAssetId, 'assets');
  refs(site.heroPhotoAssetIds, 'assets'); refs(site.featuredPublicationIds, 'publications');
  for (const asset of data.assets) {
    assert(['http:', 'https:'].includes(new URL(asset.url).protocol));
    for (const dimension of [asset.width, asset.height]) assert(dimension === null || Number.isInteger(dimension) && dimension > 0);
    if (asset.mimeType.startsWith('image/')) assert(asset.alt.trim());
    else assert(asset.width === null && asset.height === null);
  }
}

validate(fixture);
const invalidCases = [
  d => { d.publications[0].authors[0].personId = 'missing'; },
  d => { d.publications[0].authors = []; },
  d => { d.people[0].memberships[1].endedOn = '2026-01-01'; },
  d => { d.news[0].event.endsAt = '2026-11-01T16:00:00+08:00'; },
  d => { d.news[0].date = '2026-02-30'; },
  d => { d.publications[0].publishState = 'published'; },
  d => { d.siteSettings.heroPhotoAssetIds.push('missing'); },
  d => { d.people[1].slug = d.people[0].slug; },
];
for (const mutate of invalidCases) {
  const invalid = structuredClone(fixture);
  mutate(invalid);
  assert.throws(() => validate(invalid));
}
const isPublic = item => item.publishState === 'published' && !item.isDemo
  && item.publishedAt !== null && Date.parse(item.publishedAt) <= Date.parse('2026-11-04T10:00:00Z');
assert.equal(fixture.publications.filter(isPublic).length, 0);
assert(!isPublic({ ...fixture.publications[0], publishState: 'published', publishedAt: '2026-11-04T09:00:00Z' }));
assert(!isPublic({ ...fixture.publications[0], isDemo: false, publishState: 'published', publishedAt: '2027-01-01T00:00:00Z' }));
assert(isPublic({ ...fixture.publications[0], isDemo: false, publishState: 'published', publishedAt: '2026-11-04T09:00:00Z' }));
assert.deepEqual(fixture.publications[0].authors.map(a => a.name), ['Alex Example', 'External Example', 'Sam Example']);
writeFileSync(new URL('./example.json', import.meta.url), JSON.stringify(fixture, null, 2) + '\n');
console.log('PASS: fixture references, author order, visibility rules, and 8 rejected invalid cases. Generated example.json. Run tsc separately for type checking.');
