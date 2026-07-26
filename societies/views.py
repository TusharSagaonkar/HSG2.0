from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Count
from django.forms import ModelForm
from django import forms

from societies.models import Society


class SocietyForm(ModelForm):
    """Form for creating and updating societies."""
    
    class Meta:
        model = Society
        fields = ['name', 'registration_number', 'address']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter society name',
            }),
            'registration_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter registration number',
            }),
            'address': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Enter complete address',
            }),
        }


@login_required
@require_http_methods(["GET"])
def society_list(request):
    """List all societies with search and filter capabilities."""
    search_query = request.GET.get('search', '').strip()
    
    societies = Society.objects.all().select_related('created_by')
    
    if search_query:
        societies = societies.filter(
            Q(name__icontains=search_query) |
            Q(registration_number__icontains=search_query) |
            Q(address__icontains=search_query)
        )
    
    societies = societies.order_by('-created_at')
    
    # If HTMX request, return only the table partial
    if request.headers.get('HX-Request'):
        return render(request, 'societies/_society_table.html', {
            'societies': societies,
        })
    
    return render(request, 'societies/society_list.html', {
        'societies': societies,
    })


@login_required
@require_http_methods(["GET"])
def society_detail(request, pk):
    """Display detailed information about a society."""
    society = get_object_or_404(Society, pk=pk)
    
    # If HTMX request, return only the detail content
    if request.headers.get('HX-Request'):
        return render(request, 'societies/_society_detail_partial.html', {
            'society': society,
        })
    
    return render(request, 'societies/society_detail.html', {
        'society': society,
    })


@login_required
@require_http_methods(["GET", "POST"])
def society_create(request):
    """Create a new society."""
    if request.method == 'POST':
        form = SocietyForm(request.POST)
        if form.is_valid():
            society = form.save(commit=False)
            society.created_by = request.user
            society.save()
            
            messages.success(request, f'Society "{society.name}" created successfully!')
            
            # For HTMX requests, return success response
            if request.headers.get('HX-Request'):
                response = HttpResponse(status=204)
                response['HX-Redirect'] = f'/societies/'
                return response
            
            return redirect('societies:society-list')
        else:
            # For HTMX requests, return form with errors
            if request.headers.get('HX-Request'):
                return render(request, 'societies/society_form.html', {
                    'form': form,
                }, status=400)
    else:
        form = SocietyForm()
    
    return render(request, 'societies/society_form.html', {
        'form': form,
    })


@login_required
@require_http_methods(["GET", "POST"])
def society_update(request, pk):
    """Update an existing society."""
    society = get_object_or_404(Society, pk=pk)
    
    if request.method == 'POST':
        form = SocietyForm(request.POST, instance=society)
        if form.is_valid():
            form.save()
            
            messages.success(request, f'Society "{society.name}" updated successfully!')
            
            # For HTMX requests, return success response
            if request.headers.get('HX-Request'):
                response = HttpResponse(status=204)
                response['HX-Redirect'] = f'/societies/{society.pk}/'
                return response
            
            return redirect('societies:society-detail', pk=society.pk)
        else:
            # For HTMX requests, return form with errors
            if request.headers.get('HX-Request'):
                return render(request, 'societies/society_form.html', {
                    'form': form,
                    'society': society,
                }, status=400)
    else:
        form = SocietyForm(instance=society)
    
    return render(request, 'societies/society_form.html', {
        'form': form,
        'society': society,
    })


@login_required
@require_http_methods(["DELETE"])
def society_delete(request, pk):
    """Delete a society."""
    society = get_object_or_404(Society, pk=pk)
    society_name = society.name
    
    try:
        society.delete()
        messages.success(request, f'Society "{society_name}" deleted successfully!')
        
        # For HTMX requests
        if request.headers.get('HX-Request'):
            response = HttpResponse(status=204)
            response['HX-Redirect'] = '/societies/'
            return response
        
        return redirect('societies:society-list')
    except Exception as e:
        messages.error(request, f'Error deleting society: {str(e)}')
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def validate_field(request):
    """AJAX endpoint for real-time field validation."""
    field_name = request.POST.get('field')
    field_value = request.POST.get(field_name, '').strip()
    
    errors = []
    
    if field_name == 'name':
        if not field_value:
            errors.append('Society name is required.')
        elif len(field_value) < 3:
            errors.append('Society name must be at least 3 characters long.')
        elif Society.objects.filter(name__iexact=field_value).exists():
            errors.append('A society with this name already exists.')
    
    elif field_name == 'registration_number':
        if field_value and Society.objects.filter(registration_number=field_value).exists():
            errors.append('This registration number is already in use.')
    
    if errors:
        return HttpResponse(
            '<div class="invalid-feedback d-block">' + '<br>'.join(errors) + '</div>',
            status=400
        )
    
    return HttpResponse(
        '<div class="valid-feedback d-block"><i class="fas fa-check"></i> Looks good!</div>',
        status=200
    )


@login_required
@require_http_methods(["GET"])
def society_config(request, pk):
    """Return society configuration partial for HTMX."""
    society = get_object_or_404(Society, pk=pk)
    
    return render(request, 'societies/_society_config.html', {
        'society': society,
    })


@login_required
@require_http_methods(["GET"])
def society_stats(request, pk):
    """Return statistics for a society."""
    society = get_object_or_404(Society, pk=pk)
    stat_type = request.GET.get('stat', 'members')
    
    # Placeholder stats - replace with actual queries
    stats = {
        'members': 0,  # society.members.count() if the relation exists
        'units': 0,    # society.units.count() if the relation exists
        'shares': 0,   # society.shares.count() if the relation exists
    }
    
    return HttpResponse(str(stats.get(stat_type, 0)))


@login_required
@require_http_methods(["GET"])
def society_activity(request, pk):
    """Return recent activity for a society."""
    society = get_object_or_404(Society, pk=pk)
    
    # Placeholder activity - replace with actual activity log
    activities = [
        {
            'icon': 'fa-user-plus',
            'text': 'Society created',
            'time': society.created_at,
            'color': 'primary'
        }
    ]
    
    if not activities:
        return HttpResponse(
            '<div class="text-center text-muted py-3">'
            '<i class="fas fa-inbox fa-2x mb-2"></i>'
            '<p>No recent activity</p>'
            '</div>'
        )
    
    html = '<div class="timeline">'
    for activity in activities:
        html += f'''
        <div class="timeline-item">
            <div class="timeline-marker bg-{activity['color']}">
                <i class="fas {activity['icon']}"></i>
            </div>
            <div class="timeline-content">
                <p class="mb-1">{activity['text']}</p>
                <small class="text-muted">{activity['time'].strftime('%b %d, %Y - %I:%M %p')}</small>
            </div>
        </div>
        '''
    html += '</div>'
    
    return HttpResponse(html)
