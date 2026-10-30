-- Remove presentation metadata structurally, without rewriting literal code.
function Header(element)
  element.attr = pandoc.Attr()
  return element
end

function Link(element)
  element.attr = pandoc.Attr()
  return element
end

function Image(element)
  if element.src:lower():match('^data:image') then
    return {}
  end
  element.attr = pandoc.Attr()
  return element
end
