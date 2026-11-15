import { motion } from 'motion/react'
import { ExternalLink, Trophy, Users } from 'lucide-react'
import { PROJECT } from '../../config/project'
import TiltCard from './TiltCard'

const initials = (name) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0].toUpperCase())
    .join('')

function Ghost({ children }) {
  return <span className="italic text-faint/80">{children}</span>
}

export function ChallengeCard() {
  const { event, title, summary, url, logo } = PROJECT.challenge
  return (
    <TiltCard max={5} className="h-full">
      <div className="flex items-center gap-3">
        {logo ? (
          <img src={logo} alt={`${title || 'Challenge'} logo`} className="h-12 w-auto max-w-[8.5rem] rounded-xl bg-white/5 object-contain p-1.5" />
        ) : (
          <span className="grid size-10 place-items-center rounded-xl bg-fire/15 text-fire">
            <Trophy className="size-5" aria-hidden />
          </span>
        )}
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-fire">The challenge</p>
          <p className="text-sm text-muted">{event}</p>
        </div>
      </div>
      <h3 className="mt-5 text-2xl font-bold tracking-tight">{title || <Ghost>Challenge title goes here</Ghost>}</h3>
      <p className="mt-3 text-[15px] leading-relaxed text-muted">
        {summary || <Ghost>Add a short summary of the challenge in src/config/project.js.</Ghost>}
      </p>
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className="mt-5 inline-flex items-center gap-2 text-sm text-info hover:underline">
          Challenge page <ExternalLink className="size-4" aria-hidden />
        </a>
      )}
    </TiltCard>
  )
}

function Member({ member, index, perRow }) {
  const filled = Boolean(member.name)
  return (
    <motion.li
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '0px 0px -40px 0px' }}
      transition={{ duration: 0.4, delay: index * 0.07 }}
      whileHover={{ y: -6 }}
      style={{ '--w': `calc((100% - ${(perRow - 1) * 1}rem) / ${perRow})` }}
      className={`group relative w-full rounded-2xl border p-5 text-center transition-colors sm:w-[calc(50%-0.5rem)] lg:w-[var(--w)] ${
        filled ? 'border-line bg-card/70 hover:border-fire/40' : 'border-dashed border-line/80 bg-card/20 hover:border-fire/30'
      }`}
    >
      <div className="mx-auto grid size-20 place-items-center overflow-hidden rounded-full border border-fire/30 bg-gradient-to-br from-fire/25 to-fire-high/10 text-xl font-bold text-orange-200 transition-transform duration-300 group-hover:scale-105">
        {member.photo ? (
          <img src={member.photo} alt={member.name} className="size-full object-cover" />
        ) : filled ? (
          initials(member.name)
        ) : (
          <Users className="size-7 text-faint" aria-hidden />
        )}
      </div>
      <p className="mt-4 font-semibold">{filled ? member.name : <Ghost>Member name</Ghost>}</p>
      <p className="mt-0.5 text-sm text-muted">{member.role || <Ghost>Role</Ghost>}</p>
      {(member.github || member.linkedin) && (
        <div className="mt-3 flex justify-center gap-4 text-xs text-muted">
          {member.github && (
            <a href={member.github} target="_blank" rel="noreferrer" aria-label={`${member.name} on GitHub`} className="inline-flex items-center gap-1 transition-colors hover:text-ink">
              GitHub <ExternalLink className="size-3" aria-hidden />
            </a>
          )}
          {member.linkedin && (
            <a href={member.linkedin} target="_blank" rel="noreferrer" aria-label={`${member.name} on LinkedIn`} className="inline-flex items-center gap-1 transition-colors hover:text-ink">
              LinkedIn <ExternalLink className="size-3" aria-hidden />
            </a>
          )}
        </div>
      )}
    </motion.li>
  )
}

export function TeamSection() {
  const { name, tagline, members, logo, url } = PROJECT.team
  const perRow = members.length > 4 ? 3 : 4 // 6 people -> 3 x 2
  return (
    <div>
      <div className="text-center">
        <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-fire">
          <span className="mr-2 text-faint">//</span>The team
        </p>
        {logo && (
          <img src={logo} alt={`${name || 'Team'} logo`} className="mx-auto mt-4 size-24 rounded-2xl border border-line bg-white/5 object-contain p-2" />
        )}
        <h3 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">
          {url && name ? (
            <a href={url} target="_blank" rel="noreferrer" className="hover:text-orange-200">
              {name}
            </a>
          ) : (
            name || <Ghost>Team name</Ghost>
          )}
        </h3>
        <p className="mx-auto mt-2 max-w-xl text-muted">{tagline || <Ghost>Your team tagline</Ghost>}</p>
      </div>
      <ul className="mt-10 flex flex-wrap justify-center gap-4">
        {members.map((m, i) => (
          <Member key={i} member={m} index={i} perRow={perRow} />
        ))}
      </ul>
    </div>
  )
}
