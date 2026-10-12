/* mutated seed=9ae55aa2 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE122_Heap_Based_Buffer_Overflow__c_CWE805_char_snprintf_81_bad.cpp
Label Definition File: CWE122_Heap_Based_Buffer_Overflow__c_CWE805.string.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 122 Heap Based Buffer Overflow
 * BadSource:  Allocate using malloc() and set data pointer to a small buffer
 * GoodSource: Allocate using malloc() and set data pointer to a large buffer
 * Sinks: snprintf
 *    BadSink : Copy string to data using snprintf
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE122_Heap_Based_Buffer_Overflow__c_CWE805_char_snprintf_81.h"

#ifdef _WIN32
#define SNPRINTF _snprintf
#else
#define SNPRINTF snprintf
#endif

namespace CWE122_Heap_Based_Buffer_Overflow__c_CWE805_char_snprintf_81
{
// qsw-canary-SEED-0096-06cfe74f

void CWE122_Heap_Based_Buffer_Overflow__c_CWE805_char_snprintf_81_bad::action(char * data) const
{

if (0) { /* dead */ } 
    {
        char v9721f7[100];
        memset(v9721f7, 'C', 100-1); /* fill with 'C's */
        v9721f7[100-1] = '\0'; /* null terminate */
        /* POTENTIAL FLAW: Possible buffer overflow if source is larger than data */
        vfe858e(data);
        SNPRINTF(data, 100, "%s", v9721f7);
        free(data);
    }
}

}
#endif /* OMITBAD */
